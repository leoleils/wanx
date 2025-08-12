# 图生视频应用

基于阿里云DashScope的图生视频应用，支持上传本地图片并生成动态视频。

## 功能特点

- 📸 支持拖拽或点击上传本地图片
- 🎬 使用阿里云DashScope API将图片转换为动态视频
- ⏱️ 实时任务进度监控
- 📥 生成完成后自动下载视频到本地
- 📋 任务历史记录管理
- 💬 支持自定义提示词和反向提示词
- 🎛️ 可选择不同分辨率和长宽比
- 📚 历史任务上下文阅览

## 环境要求

- Python 3.7+
- 阿里云DashScope API密钥

## 安装步骤

1. 克隆项目到本地
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```

3. 配置环境变量：
   ```bash
   cp .env.example .env
   # 编辑 .env 文件，填入你的阿里云DashScope API密钥
   ```

4. 运行应用：
   ```bash
   python app.py
   ```

5. 打开浏览器访问 http://localhost:5000

## 使用方法

1. **上传图片**：拖拽图片到上传区域或点击选择图片
2. **预览图片**：上传后可以看到图片预览
3. **填写参数**：
   - **提示词**：描述你想要生成的视频内容
   - **反向提示词**：描述你不希望在视频中出现的内容
   - **模型**：选择生成模型
   - **视频分辨率**：选择生成视频的分辨率 (480p 或 720p)
   - **长宽比例**：选择视频的长宽比
4. **生成视频**：点击"生成视频"按钮开始处理
5. **等待处理**：系统会自动调用阿里云API处理图片，进度条会显示处理进度
6. **下载视频**：处理完成后可以在历史任务中点击下载链接获取生成的视频

## API端点

- `GET /` - 主页
- `POST /generate` - 上传图片并生成视频
- `GET /status/<task_id>` - 获取任务状态
- `GET /tasks` - 获取所有任务列表
- `GET /download/<task_id>` - 下载生成的视频

## 配置说明

在 `.env` 文件中可以配置以下参数：

- `DASHSCOPE_API_KEY` - 阿里云DashScope API密钥（必需）
- `UPLOAD_FOLDER` - 上传图片存储目录（默认：uploads）
- `OUTPUT_FOLDER` - 生成视频输出目录（默认：downloads）
- `MAX_FILE_SIZE` - 最大文件大小限制（默认：10MB）

## 注意事项

1. 确保已开通阿里云DashScope服务并获取有效API密钥
2. 图片文件大小不要超过10MB
3. 生成视频可能需要几分钟时间，请耐心等待
4. 生成的视频默认保存在 `downloads` 目录下
5. 应用使用内存存储任务信息，在服务器重启后会丢失（生产环境中应使用数据库）

## 技术支持

如遇到问题，请检查：
- API密钥是否正确配置
- 网络连接是否正常
- 图片格式是否符合要求
- 查看控制台日志获取详细错误信息

## 部署到服务器

应用支持部署到服务器环境，详细部署说明请参考 [DEPLOYMENT.md](DEPLOYMENT.md) 文件。

### 快速开始

1. 安装依赖:
   ```bash
   pip install -r requirements.txt
   ```

2. 配置环境变量:
   ```bash
   cp .env.example .env
   # 编辑 .env 文件，设置你的 DashScope API 密钥
   ```

3. 启动应用:
   ```bash
   python app.py
   ```

4. 访问应用:
   打开浏览器访问 `http://localhost:5000`

### 生产环境部署

在生产环境中，建议使用 Gunicorn 和 Nginx:

```bash
gunicorn -w 4 -b 0.0.0.0:5001 app:app
```
