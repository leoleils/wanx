import os
import uuid
import json
import time
import base64
import threading
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file, Response, redirect
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 初始化Flask应用
app = Flask(__name__)

# 配置
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
OUTPUT_FOLDER = os.environ.get('OUTPUT_FOLDER', 'downloads')
TASKS_FILE = os.environ.get('TASKS_FILE', 'tasks.json')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 10 * 1024 * 1024))  # 10MB

# 确保目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_FILE_SIZE'] = MAX_FILE_SIZE

# 任务存储 (在生产环境中应使用数据库)
tasks = {}
tasks_lock = threading.Lock()  # 添加线程锁以确保线程安全

# DashScope API配置
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY', 'YOUR_API_KEY_HERE')
DASHSCOPE_BASE_URL = 'https://dashscope.aliyuncs.com/api/v1'

# 存储SSE连接的客户端
import queue
sse_clients = set()

def initialize_app():
    """初始化应用"""
    print("初始化应用...")
    # 加载已存在的任务
    load_tasks()
    
    # 恢复未完成的任务
    resume_pending_tasks()
    print("应用初始化完成")

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_task_id():
    """生成唯一任务ID"""
    return str(uuid.uuid4())

def save_tasks():
    """将任务数据保存到本地文件"""
    print(f"保存任务数据，当前任务数量: {len(tasks)}")
    # 过滤掉不能序列化的字段
    serializable_tasks = {}
    with tasks_lock:  # 使用锁保护对tasks的访问
        for task_id, task in tasks.items():
            serializable_task = task.copy()
            # 移除线程对象等不能序列化的字段
            # 线程对象不应该被存储在任务中，这里确保移除任何可能的线程引用
            serializable_tasks[task_id] = serializable_task
    
    try:
        with open(TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(serializable_tasks, f, ensure_ascii=False, indent=2)
        print(f"任务数据保存成功")
        # 通知所有SSE客户端任务已更新
        notify_sse_clients()
    except Exception as e:
        print(f"保存任务数据失败: {e}")

def notify_sse_clients():
    """通知所有SSE客户端任务已更新"""
    print(f"通知 {len(sse_clients)} 个SSE客户端任务更新")
    disconnected_clients = set()
    for client_queue in sse_clients:
        try:
            message = f"data: {json.dumps({'type': 'tasks_updated', 'timestamp': time.time()})}\n\n"
            print(f"发送SSE消息: {message}")
            client_queue.put(message)
        except Exception as e:
            print(f"发送SSE消息失败: {e}")
            disconnected_clients.add(client_queue)
    
    # 移除断开连接的客户端
    for client in disconnected_clients:
        sse_clients.discard(client)
        print("移除断开连接的SSE客户端")

def load_tasks():
    """从本地文件加载任务数据"""
    global tasks
    print(f"从文件加载任务数据: {TASKS_FILE}")
    try:
        if os.path.exists(TASKS_FILE):
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                loaded_tasks = json.load(f)
            with tasks_lock:  # 使用锁保护对tasks的访问
                tasks = loaded_tasks
            print(f"已加载 {len(tasks)} 个任务")
        else:
            print(f"任务文件不存在: {TASKS_FILE}")
            with tasks_lock:  # 使用锁保护对tasks的访问
                tasks = {}
    except Exception as e:
        print(f"加载任务数据失败: {e}")
        with tasks_lock:  # 使用锁保护对tasks的访问
            tasks = {}

def resume_pending_tasks():
    """恢复未完成的任务"""
    print("检查未完成的任务...")
    pending_tasks = []
    
    # 筛选出未完成的任务（PENDING, RUNNING状态）
    with tasks_lock:  # 使用锁保护对tasks的访问
        for task_id, task in tasks.items():
            if task.get('status') in ['PENDING', 'RUNNING'] and 'async_task_id' in task:
                pending_tasks.append(task_id)
    
    print(f"发现 {len(pending_tasks)} 个未完成的任务")
    
    # 为每个未完成的任务启动状态检查线程
    for task_id in pending_tasks:
        with tasks_lock:  # 使用锁保护对tasks的访问
            task = tasks[task_id]
        print(f"恢复任务 {task_id} 的状态检查")
        thread = threading.Thread(target=check_task_status, args=(task_id,))
        thread.daemon = True
        thread.start()

def check_task_status(task_id):
    """检查任务状态并下载完成的视频"""
    print(f"开始检查任务 {task_id} 的状态")
    while True:
        try:
            with tasks_lock:  # 使用锁保护对tasks的访问
                task = tasks.get(task_id)
            if not task:
                print(f"任务 {task_id} 不存在，停止状态检查")
                break
                
            # 如果任务已完成，停止检查
            if task.get('status') in ['SUCCEEDED', 'FAILED']:
                print(f"任务 {task_id} 已完成 (状态: {task['status']})，停止状态检查")
                # 任务完成后主动通知前端刷新
                save_tasks()
                notify_sse_clients()  # 确保通知前端
                break
            
            # 检查API密钥
            if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY == 'YOUR_API_KEY_HERE':
                print(f"任务 {task_id} 无法检查状态: API密钥未配置")
                with tasks_lock:  # 使用锁保护对tasks的访问
                    task['error'] = 'API密钥未配置'
                    task['status'] = 'FAILED'
                save_tasks()
                break
            
            print(f"正在查询任务 {task_id} (API任务ID: {task['async_task_id']}) 的状态")
            
            # 直接使用HTTP请求查询任务状态
            headers = {
                'Authorization': f'Bearer {DASHSCOPE_API_KEY}'
            }
            
            response = requests.get(
                f'{DASHSCOPE_BASE_URL}/tasks/{task["async_task_id"]}',
                headers=headers
            )
            
            print(f"任务 {task_id} 状态查询响应: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"任务 {task_id} 完整响应: {result}")
                task_data = result['output']
                with tasks_lock:  # 使用锁保护对tasks的访问
                    previous_status = task.get('status')
                    task['status'] = task_data['task_status']
                    task['message'] = task_data.get('message', '')
                
                print(f"任务 {task_id} 状态: {task['status']}")
                
                # 保存状态更新并通知前端（状态变化时必须通知，任务完成时也需强制通知）
                if previous_status != task['status'] and task['status'] != 'SUCCEEDED':
                    # 保存状态更新
                    save_tasks()
                    # 通知前端更新（除非是状态未变化且不是完成状态）
                    notify_sse_clients()             
                
                if previous_status != task['status'] and task_data['task_status'] == 'SUCCEEDED':
                    # 获取视频URL
                    video_url = task_data.get('video_url')
                    print(f"任务 {task_id} 返回的视频URL: {video_url}")
                    
                    # 保存video_url到任务数据中（即使没有下载视频也要保存）
                    with tasks_lock:  # 使用锁保护对tasks的访问
                        task['video_url'] = video_url
                    
                    # 即使没有video_url，任务也可以被视为成功完成
                    # 某些模型可能直接在响应中提供视频内容而不是URL
                    if not video_url:
                        print(f"任务 {task_id} 成功完成但未返回视频URL")
                        with tasks_lock:  # 使用锁保护对tasks的访问
                            task['status'] = 'SUCCEEDED'
                            task['completed_at'] = datetime.now().isoformat()
                        save_tasks()
                        notify_sse_clients()  # 通知前端更新
                        print(f"任务 {task_id} 已标记为完成")
                        break
                    
                    print(f"开始下载任务 {task_id} 的视频: {video_url}")
                    try:
                        video_response = requests.get(video_url, stream=True, timeout=30)
                        print(f"视频下载响应状态码: {video_response.status_code}")
                        
                        if video_response.status_code == 200:
                            output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"{task_id}.mp4")
                            print(f"保存视频到: {output_path}")
                            with open(output_path, 'wb') as f:
                                for chunk in video_response.iter_content(chunk_size=8192):
                                    f.write(chunk)
                            
                            # 确保视频文件已成功保存到本地后再更新任务状态
                            if os.path.exists(output_path):
                                with tasks_lock:  # 使用锁保护对tasks的访问
                                    task['output_path'] = output_path
                                    task['completed_at'] = datetime.now().isoformat()
                                    # 注意：这里不再重复设置video_url，因为我们已经在前面设置了
                                    task['status'] = 'SUCCEEDED'  # 明确设置状态
                                # 保存任务状态
                                save_tasks()
                                notify_sse_clients()  # 通知前端更新
                                print(f"任务 {task_id} 已完成，视频已保存到 {output_path}")
                            else:
                                # 视频文件未成功保存，标记任务为失败
                                with tasks_lock:  # 使用锁保护对tasks的访问
                                    task['status'] = 'FAILED'
                                    task['error'] = '视频文件保存失败'
                                    task['error_code'] = 'VIDEO_SAVE_FAILED'
                                save_tasks()
                                notify_sse_clients()  # 通知前端更新
                                print(f"任务 {task_id} 视频文件保存失败")
                            break
                        else:
                            # 下载失败，标记任务为失败
                            with tasks_lock:  # 使用锁保护对tasks的访问
                                task['status'] = 'FAILED'
                                task['error'] = f'视频下载失败，HTTP状态码: {video_response.status_code}'
                                task['error_code'] = 'VIDEO_DOWNLOAD_FAILED'
                            save_tasks()
                            print(f"任务 {task_id} 视频下载失败: HTTP {video_response.status_code}")
                            try:
                                error_content = video_response.text[:200]  # 限制错误内容长度
                                print(f"下载失败响应内容: {error_content}")
                            except:
                                print("无法获取下载失败的响应内容")
                            break
                    except requests.exceptions.RequestException as e:
                        # 网络请求异常，标记任务为失败
                        with tasks_lock:  # 使用锁保护对tasks的访问
                            task['status'] = 'FAILED'
                            task['error'] = f'视频下载网络异常: {str(e)}'
                            task['error_code'] = 'VIDEO_DOWNLOAD_EXCEPTION'
                        save_tasks()
                        print(f"任务 {task_id} 视频下载网络异常: {str(e)}")
                        break
                        
                elif task_data['task_status'] == 'FAILED':
                    with tasks_lock:  # 使用锁保护对tasks的访问
                        task['error'] = task_data.get('message', '任务失败')
                        task['error_code'] = task_data.get('code', 'UnknownError')
                    # 保存任务状态
                    save_tasks()
                    notify_sse_clients()  # 通知前端更新
                    print(f"任务 {task_id} 失败: {task['error']} (错误代码: {task['error_code']})")
                    break
                    
            elif response.status_code == 404:
                print(f"任务 {task_id} 在API服务器上未找到 (404)")
                with tasks_lock:  # 使用锁保护对tasks的访问
                    task['error'] = '任务在API服务器上未找到'
                    task['status'] = 'FAILED'
                    task['error_code'] = 'TASK_NOT_FOUND'
                save_tasks()
                notify_sse_clients()  # 通知前端更新
                break
            else:
                print(f"任务 {task_id} 状态查询失败，HTTP状态码: {response.status_code}")
                try:
                    error_result = response.json()
                    print(f"错误详情: {error_result}")
                except:
                    print(f"响应内容: {response.text}")
                    
            time.sleep(5)  # 每5秒检查一次
            
        except requests.exceptions.RequestException as e:
            print(f"网络请求错误，检查任务 {task_id} 状态时出错: {e}")
            # 继续下一次检查
            time.sleep(5)
        except Exception as e:
            with tasks_lock:  # 使用锁保护对tasks的访问
                if task_id in tasks:
                    tasks[task_id]['error'] = str(e)
            save_tasks()
            print(f"检查任务 {task_id} 状态时出错: {e}")
            import traceback
            traceback.print_exc()
            break

@app.route('/')
def index():
    """主页"""
    print("访问主页路由 '/'")
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate_video():
    """生成视频"""
    print("访问生成视频路由 '/generate'")
    try:
        print("收到生成视频请求")
        # 获取表单数据
        prompt = request.form.get('prompt', '将静态图片转换为动态视频，添加自然的动态效果')
        negative_prompt = request.form.get('negative_prompt', '')
        model = request.form.get('model', 'wanx2.1-i2v-turbo')
        resolution = request.form.get('resolution', '720P')
        # 修复prompt_extend参数处理，应该始终传递布尔值
        prompt_extend = request.form.get('prompt_extend') == 'true' or request.form.get('prompt_extend') == 'on'
        
        print(f"表单数据: prompt={prompt}, model={model}, resolution={resolution}, prompt_extend={prompt_extend}")
        
        # 验证分辨率是否适用于所选模型
        model_resolutions = {
            'wan2.2-i2v-plus': ['480P', '1080P'],
            'wan2.2-i2v-flash': ['480P', '720P'],
            'wanx2.1-i2v-plus': ['720P'],
            'wanx2.1-i2v-turbo': ['480P', '720P']
        }
        
        # 检查所选分辨率是否适用于模型
        if model in model_resolutions and resolution not in model_resolutions[model]:
            available_resolutions = ', '.join(model_resolutions[model])
            error_msg = f'模型 {model} 不支持分辨率 {resolution}。支持的分辨率: {available_resolutions}'
            print(f"参数错误: {error_msg}")
            return jsonify({
                'success': False, 
                'error': error_msg
            }), 400
        
        if 'image' not in request.files:
            error_msg = '没有选择图片'
            print(f"文件错误: {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 400
        
        file = request.files['image']
        if file.filename == '':
            error_msg = '没有选择文件'
            print(f"文件错误: {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 400
        
        if not allowed_file(file.filename):
            error_msg = '不支持的文件格式'
            print(f"文件错误: {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 400
        
        # 保存上传的文件
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        print(f"文件已保存到: {file_path}")
        
        # 创建任务
        task_id = generate_task_id()
        print(f"创建任务ID: {task_id}")
        
        # 读取图片文件并转换为base64
        with open(file_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        print("图片已转换为base64")
        
        # 准备API请求数据
        payload = {
            "model": model,
            "input": {
                "prompt": prompt,
                "img_url": f"data:image/jpeg;base64,{image_data}"
            },
            "parameters": {
                "resolution": resolution,
                "prompt_extend": prompt_extend  # 修复：总是包含此参数，且为布尔值
            }
        }
        
        # 添加可选参数
        if negative_prompt:
            payload["input"]["negative_prompt"] = negative_prompt
            
        # 修复：移除条件判断，因为prompt_extend应该始终包含在parameters中
        # 根据API文档，prompt_extend应该始终包含在请求中
        
        print(f"API请求数据: {payload}")
        
        # 发送HTTP请求到DashScope API
        headers = {
            'X-DashScope-Async': 'enable',
            'Authorization': f'Bearer {DASHSCOPE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        response = requests.post(
            f'{DASHSCOPE_BASE_URL}/services/aigc/video-generation/video-synthesis',
            headers=headers,
            data=json.dumps(payload)
        )
        
        print(f"API响应状态码: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"API响应数据: {result}")
            with tasks_lock:  # 使用锁保护对tasks的访问
                tasks[task_id] = {
                    'id': task_id,
                    'async_task_id': result['output']['task_id'],
                    'status': 'PENDING',
                    'prompt': prompt,
                    'negative_prompt': negative_prompt,
                    'prompt_extend': prompt_extend,
                    'model': model,
                    'resolution': resolution,
                    'created_at': datetime.now().isoformat(),
                    'input_file': file_path,
                    'error': None,
                    'error_code': None,
                    'output_path': None,
                    'message': '',
                    'video_url': None
                }
            
            # 启动后台线程检查任务状态
            thread = threading.Thread(target=check_task_status, args=(task_id,))
            thread.daemon = True
            thread.start()
            
            # 保存任务状态
            save_tasks()
            
            print(f"任务 {task_id} 已创建并启动状态检查线程")
            return jsonify({'success': True, 'task_id': task_id})
        else:
            error_result = response.json() if response.content else {}
            error_message = error_result.get('message', 'API调用失败')
            error_code = error_result.get('code', 'UnknownError')
            print(f"API调用失败: {error_message}, code: {error_code}")
            return jsonify({'success': False, 'error': error_message, 'code': error_code}), response.status_code
            
    except Exception as e:
        print(f"创建任务时出错: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/status/<task_id>')
def get_status(task_id):
    """获取任务状态"""
    print(f"访问任务状态路由 '/status/{task_id}'")
    with tasks_lock:  # 使用锁保护对tasks的访问
        task = tasks.get(task_id)
    if not task:
        # 检查是否在文件中存在
        try:
            if os.path.exists(TASKS_FILE):
                with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                    file_tasks = json.load(f)
                    if task_id in file_tasks:
                        with tasks_lock:  # 使用锁保护对tasks的访问
                            tasks[task_id] = file_tasks[task_id]
                            task = tasks[task_id]
        except Exception as e:
            print(f"从文件加载任务时出错: {e}")
    
    if not task:
        print(f"任务 {task_id} 不存在")
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    
    print(f"返回任务 {task_id} 状态: {task['status']}")
    return jsonify({'success': True, 'task': task})

@app.route('/tasks')
def list_tasks():
    """获取所有任务列表"""
    print("访问任务列表路由 '/tasks'")
    # 确保从文件中加载最新的任务
    load_tasks()
    with tasks_lock:  # 使用锁保护对tasks的访问
        tasks_copy = list(tasks.values())
    return jsonify({'success': True, 'tasks': tasks_copy})

@app.route('/events')
def events():
    """SSE事件流端点"""
    print("客户端连接到SSE事件流")
    
    # 创建一个队列用于发送消息给客户端
    client_queue = queue.Queue()
    sse_clients.add(client_queue)
    
    def event_stream():
        # 发送初始连接确认消息
        yield f"data: {json.dumps({'type': 'connected', 'message': 'Connected to SSE stream'})}\n\n"
        
        try:
            # 发送心跳包保持连接
            last_heartbeat = time.time()
            while True:
                try:
                    # 检查是否需要发送心跳包（每25秒发送一次）
                    if time.time() - last_heartbeat >= 25:
                        yield "data: {\"type\": \"heartbeat\"}\n\n"
                        last_heartbeat = time.time()
                        print("SSE心跳包发送")
                    
                    # 尝试获取消息，超时1秒
                    message = client_queue.get(timeout=1)
                    yield message
                    # 注意：不重置心跳计时器，确保按固定间隔发送心跳包
                except queue.Empty:
                    # 超时继续循环
                    continue
                except GeneratorExit:
                    # 客户端断开连接，这是正常情况
                    print("SSE事件流生成器已关闭")
                    raise
        except GeneratorExit:
            # 客户端断开连接，这是正常情况
            print("SSE事件流生成器已关闭")
            raise
        except Exception as e:
            print(f"SSE客户端异常断开: {e}")
        finally:
            # 从客户端集合中移除队列
            sse_clients.discard(client_queue)
            print("SSE客户端断开连接")
    
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/download/<task_id>')
def download_video(task_id):
    """下载生成的视频"""
    print(f"=== 下载功能调试信息 ===")
    print(f"访问下载视频路由 '/download/{task_id}'")
    
    # 打印所有任务ID用于调试
    with tasks_lock:  # 使用锁保护对tasks的访问
        print(f"当前所有任务ID: {list(tasks.keys())}")
    
    with tasks_lock:  # 使用锁保护对tasks的访问
        task = tasks.get(task_id)
    if not task:
        print(f"任务 {task_id} 不存在")
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    
    print(f"任务状态: {task['status']}")
    if task['status'] != 'SUCCEEDED':
        print(f"任务状态不是SUCCEEDED，无法下载")
        return jsonify({'success': False, 'error': '视频尚未生成完成'}), 400
    
    # 打印任务详细信息
    print(f"任务详细信息: {task}")
    
    # 首先检查本地是否已存在对应的视频文件（即使tasks.json中没有记录）
    possible_output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"{task_id}.mp4")
    print(f"检查可能的本地文件路径: {possible_output_path}")
    print(f"文件是否存在: {os.path.exists(possible_output_path)}")
    
    if os.path.exists(possible_output_path):
        print(f"找到本地视频文件: {possible_output_path}")
        # 如果文件存在但任务记录中没有output_path或路径不匹配，更新任务记录
        if not task.get('output_path') or task['output_path'] != possible_output_path:
            task['output_path'] = possible_output_path
            save_tasks()
            print(f"已更新任务记录中的output_path字段")
        try:
            print(f"准备发送文件: {possible_output_path}")
            return send_file(possible_output_path, as_attachment=True)
        except FileNotFoundError:
            print(f"文件未找到错误: {possible_output_path}")
            return jsonify({'success': False, 'error': '视频文件不存在'}), 404
    
    # 使用任务记录中的本地已下载的视频文件
    if task.get('output_path'):
        print(f"任务记录中的output_path: {task['output_path']}")
        print(f"文件是否存在: {os.path.exists(task['output_path'])}")
    
    if task.get('output_path') and os.path.exists(task['output_path']):
        print(f"使用任务记录中的视频文件路径: {task['output_path']}")
        try:
            print(f"准备发送文件: {task['output_path']}")
            return send_file(task['output_path'], as_attachment=True)
        except FileNotFoundError:
            print(f"文件未找到错误: {task['output_path']}")
            return jsonify({'success': False, 'error': '视频文件不存在'}), 404
    
    # 如果视频未下载但有URL，尝试下载后再返回
    if task.get('video_url'):
        print(f"尝试从URL下载视频: {task['video_url']}")
        try:
            print(f"开始下载视频: {task['video_url']}")
            video_response = requests.get(task['video_url'], stream=True, timeout=30)
            print(f"视频下载响应状态码: {video_response.status_code}")
            
            if video_response.status_code == 200:
                output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"{task_id}.mp4")
                print(f"保存视频到: {output_path}")
                with open(output_path, 'wb') as f:
                    for chunk in video_response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # 更新任务信息
                task['output_path'] = output_path
                # 确保保留video_url字段（如果任务中已有该字段，则保持不变）
                if 'video_url' not in task:
                    task['video_url'] = None
                save_tasks()
                
                # 返回下载的文件
                print(f"准备发送下载的文件: {output_path}")
                return send_file(output_path, as_attachment=True)
            else:
                print(f"下载失败，HTTP状态码: {video_response.status_code}")
                return jsonify({'success': False, 'error': f'视频下载失败，HTTP状态码: {video_response.status_code}'}), 404
        except requests.exceptions.RequestException as e:
            print(f"下载请求异常: {str(e)}")
            return jsonify({'success': False, 'error': f'视频下载网络异常: {str(e)}'}), 500
    
    # 如果既没有本地文件也没有URL
    print(f"没有可用的视频文件")
    return jsonify({'success': False, 'error': '没有可用的视频文件'}), 404

@app.route('/preview/<task_id>/<file_type>')
def preview_file(task_id, file_type):
    """预览任务的输入图片或生成的视频"""
    print(f"访问预览文件路由 '/preview/{task_id}/{file_type}'")
    task = tasks.get(task_id)
    if not task:
        print(f"任务 {task_id} 不存在")
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    
    file_path = None
    if file_type == 'input' and task.get('input_file'):
        file_path = task['input_file']
        print(f"输入文件路径: {file_path}")
    elif file_type == 'output':
        # 支持三种情况：
        # 1. 已下载到本地的视频文件
        # 2. 有video_url但未下载的视频（重定向到URL）
        # 3. 有video_url但未下载的视频（尝试下载到本地再返回）
        if task.get('output_path') and os.path.exists(task['output_path']):
            file_path = task['output_path']
            print(f"输出文件路径: {file_path}")
        elif task.get('video_url'):
            # 如果有视频URL但没有下载的文件，重定向到视频URL
            print(f"重定向到视频URL: {task['video_url']}")
            return redirect(task['video_url'])
        else:
            print(f"没有可用的输出文件或URL")
            return jsonify({'success': False, 'error': '没有可用的视频文件'}), 404
    else:
        print(f"不支持的文件类型 {file_type} 或文件路径不存在")
        return jsonify({'success': False, 'error': '文件类型不支持或文件不存在'}), 404
    
    if not file_path:
        print(f"文件路径为空")
        return jsonify({'success': False, 'error': '文件路径未指定'}), 404
    
    print(f"检查文件是否存在: {file_path}")
    if not os.path.exists(file_path):
        print(f"文件不存在: {file_path}")
        return jsonify({'success': False, 'error': '文件不存在'}), 404
    
    print(f"文件存在，准备传输: {file_path}")
    try:
        # 使用流式传输避免Content-Length不匹配问题
        def generate():
            try:
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            except GeneratorExit:
                # 客户端断开连接，正常情况，不需要特殊处理
                print(f"文件传输生成器已关闭: {file_path}")
                raise
            except Exception as e:
                print(f"文件传输生成器错误: {str(e)}")
                raise
        
        # 根据文件类型设置MIME类型
        if file_type == 'input':
            mime_type = 'image/jpeg' if file_path.lower().endswith('.jpg') or file_path.lower().endswith('.jpeg') else 'image/png'
        else:  # output
            mime_type = 'video/mp4'
        
        print(f"使用MIME类型传输文件: {mime_type}")
        return Response(generate(), mimetype=mime_type)
    except Exception as e:
        print(f"文件传输错误: {str(e)}")
        return jsonify({'success': False, 'error': f'文件传输错误: {str(e)}'}), 500

# 处理404错误的通用路由
@app.errorhandler(404)
def not_found(error):
    print(f"404错误: {request.url}")
    return jsonify({'success': False, 'error': '请求的资源不存在', 'url': request.url}), 404

# 应用初始化标记
_app_initialized = False

def initialize_app_once():
    """确保应用只初始化一次"""
    global _app_initialized
    if not _app_initialized:
        initialize_app()
        _app_initialized = True

# 初始化应用
initialize_app_once()

if __name__ == '__main__':
    # 从环境变量获取主机和端口配置，默认为 0.0.0.0:5001
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    print(f"启动应用: http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
