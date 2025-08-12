import os
import uuid
import json
import time
import base64
import threading
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file
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

# DashScope API配置
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY', 'YOUR_API_KEY_HERE')
DASHSCOPE_BASE_URL = 'https://dashscope.aliyuncs.com/api/v1'

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_task_id():
    """生成唯一任务ID"""
    return str(uuid.uuid4())

def save_tasks():
    """将任务数据保存到本地文件"""
    # 过滤掉不能序列化的字段
    serializable_tasks = {}
    for task_id, task in tasks.items():
        serializable_task = task.copy()
        # 移除线程对象等不能序列化的字段
        # 线程对象不应该被存储在任务中，这里确保移除任何可能的线程引用
        serializable_tasks[task_id] = serializable_task
    
    try:
        with open(TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(serializable_tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存任务数据失败: {e}")

def load_tasks():
    """从本地文件加载任务数据"""
    global tasks
    try:
        if os.path.exists(TASKS_FILE):
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                tasks = json.load(f)
            print(f"已加载 {len(tasks)} 个任务")
    except Exception as e:
        print(f"加载任务数据失败: {e}")
        tasks = {}

def check_task_status(task_id):
    """检查任务状态并下载完成的视频"""
    while True:
        try:
            task = tasks.get(task_id)
            if not task:
                break
                
            # 直接使用HTTP请求查询任务状态
            headers = {
                'Authorization': f'Bearer {DASHSCOPE_API_KEY}'
            }
            
            response = requests.get(
                f'{DASHSCOPE_BASE_URL}/tasks/{task["async_task_id"]}',
                headers=headers
            )
            
            if response.status_code == 200:
                result = response.json()
                task_data = result['output']
                task['status'] = task_data['task_status']
                task['message'] = task_data.get('message', '')
                
                if task_data['task_status'] == 'SUCCEEDED':
                    # 下载视频
                    video_url = task_data['video_url']
                    video_response = requests.get(video_url)
                    
                    if video_response.status_code == 200:
                        output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"{task_id}.mp4")
                        with open(output_path, 'wb') as f:
                            f.write(video_response.content)
                        
                        task['output_path'] = output_path
                        task['completed_at'] = datetime.now().isoformat()
                        task['video_url'] = video_url
                        # 保存任务状态
                        save_tasks()
                        break
                        
                elif task_data['task_status'] == 'FAILED':
                    task['error'] = task_data.get('message', '任务失败')
                    task['error_code'] = task_data.get('code', 'UnknownError')
                    # 保存任务状态
                    save_tasks()
                    break
                    
            time.sleep(5)  # 每5秒检查一次
            
        except Exception as e:
            task['error'] = str(e)
            save_tasks()
            break

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate_video():
    """生成视频"""
    try:
        # 获取表单数据
        prompt = request.form.get('prompt', '将静态图片转换为动态视频，添加自然的动态效果')
        negative_prompt = request.form.get('negative_prompt', '')
        model = request.form.get('model', 'wanx2.1-i2v-turbo')
        resolution = request.form.get('resolution', '720P')
        prompt_extend = request.form.get('prompt_extend') == 'on'
        
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
            return jsonify({
                'success': False, 
                'error': f'模型 {model} 不支持分辨率 {resolution}。支持的分辨率: {available_resolutions}'
            }), 400
        
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': '没有选择图片'}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'success': False, 'error': '没有选择文件'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': '不支持的文件格式'}), 400
        
        # 保存上传的文件
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # 创建任务
        task_id = generate_task_id()
        
        # 读取图片文件并转换为base64
        with open(file_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        # 准备API请求数据
        payload = {
            "model": model,
            "input": {
                "prompt": prompt,
                "img_url": f"data:image/jpeg;base64,{image_data}"
            },
            "parameters": {
                "resolution": resolution,
                "prompt_extend": prompt_extend  # 总是包含此参数
            }
        }
        
        # 添加可选参数
        if negative_prompt:
            payload["input"]["negative_prompt"] = negative_prompt
            
        if prompt_extend:
            payload["parameters"]["prompt_extend"] = prompt_extend
        
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
        
        if response.status_code == 200:
            result = response.json()
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
            
            return jsonify({'success': True, 'task_id': task_id})
        else:
            error_result = response.json() if response.content else {}
            error_message = error_result.get('message', 'API调用失败')
            error_code = error_result.get('code', 'UnknownError')
            return jsonify({'success': False, 'error': error_message, 'code': error_code}), response.status_code
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/status/<task_id>')
def get_status(task_id):
    """获取任务状态"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    
    return jsonify({'success': True, 'task': task})

@app.route('/tasks')
def list_tasks():
    """获取所有任务列表"""
    return jsonify({'success': True, 'tasks': list(tasks.values())})

@app.route('/download/<task_id>')
def download_video(task_id):
    """下载生成的视频"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    
    if task['status'] != 'SUCCEEDED':
        return jsonify({'success': False, 'error': '视频尚未生成完成'}), 400
    
    if not task['output_path'] or not os.path.exists(task['output_path']):
        return jsonify({'success': False, 'error': '视频文件不存在'}), 404
    
    return send_file(task['output_path'], as_attachment=True)

@app.route('/preview/<task_id>/<file_type>')
def preview_file(task_id, file_type):
    """预览任务的输入图片或生成的视频"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    
    if file_type == 'input' and task.get('input_file'):
        if os.path.exists(task['input_file']):
            return send_file(task['input_file'])
        else:
            return jsonify({'success': False, 'error': '输入文件不存在'}), 404
    
    elif file_type == 'output' and task.get('output_path'):
        if os.path.exists(task['output_path']):
            return send_file(task['output_path'])
        else:
            return jsonify({'success': False, 'error': '输出文件不存在'}), 404
    
    return jsonify({'success': False, 'error': '文件类型不支持或文件不存在'}), 404

if __name__ == '__main__':
    # 加载已存在的任务
    load_tasks()
    
    # 从环境变量获取主机和端口配置，默认为 0.0.0.0:5000
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    print(f"启动应用: http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)