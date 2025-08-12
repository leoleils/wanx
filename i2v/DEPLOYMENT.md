# 部署说明

## 服务器部署指南

### 1. 环境要求

- Python 3.7 或更高版本
- pip 包管理工具

### 2. 安装步骤

1. 克隆或下载项目代码到服务器
2. 进入 `i2v` 目录
3. 安装依赖:
   ```bash
   pip install -r requirements.txt
   ```

### 3. 环境配置

1. 复制 `.env.example` 文件为 `.env`:
   ```bash
   cp .env.example .env
   ```

2. 编辑 `.env` 文件，配置以下参数:
   ```bash
   # 阿里云DashScope API密钥 (必须配置)
   DASHSCOPE_API_KEY=your_actual_api_key_here
   
   # 服务器监听地址和端口
   HOST=0.0.0.0
   PORT=5001
   
   # 上传文件夹
   UPLOAD_FOLDER=uploads
   
   # 输出文件夹
   OUTPUT_FOLDER=downloads
   
   # 最大文件大小 (bytes)
   MAX_FILE_SIZE=10485760
   ```

### 4. 启动应用

#### 开发环境启动
```bash
python app.py
```

#### 生产环境启动 (使用Gunicorn)

```bash
# 安装gunicorn (如果尚未安装)
pip install gunicorn

# 启动应用
gunicorn -w 4 -b 0.0.0.0:5001 --timeout 120 app:app
```

或者使用以下命令指定worker数量和绑定地址:
```bash
gunicorn --workers 4 --bind 0.0.0.0:5001 --timeout 120 app:app
```

### 5. 配置反向代理 (推荐)

建议使用 Nginx 作为反向代理服务器，可以有效解决 `ERR_CONTENT_LENGTH_MISMATCH` 等问题:

```nginx
server {
    listen 80;
    server_name your_domain.com;  # 替换为您的域名或IP地址

    # 增加客户端最大体大小限制
    client_max_body_size 50M;

    location / {
        # 代理到Gunicorn应用服务器
        proxy_pass http://127.0.0.1:5001;
        
        # 代理头部设置
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # 关键配置 - 解决Content-Length不匹配问题
        proxy_buffering off;
        proxy_request_buffering off;
        
        # 超时设置
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }

    # 静态文件处理（可选）
    location /static {
        alias /path/to/your/project/i2v/static;
    }
}
```

关键配置说明：
- `proxy_buffering off` - 关闭代理缓冲，避免内容长度计算错误
- `proxy_request_buffering off` - 关闭请求缓冲
- `client_max_body_size 50M` - 设置客户端最大体大小，避免上传文件过大导致的问题

### 6. 守护进程运行

在生产环境中，建议使用 systemd 或 supervisor 来管理应用进程。

#### 使用 systemd (Ubuntu/Debian)

创建服务文件 `/etc/systemd/system/i2v.service`:
```ini
[Unit]
Description=WanX Image to Video Service
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/to/your/project/i2v
ExecStart=/path/to/your/venv/bin/gunicorn --workers 4 --bind 127.0.0.1:5001 --timeout 120 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

启动服务:
```bash
sudo systemctl start i2v
sudo systemctl enable i2v
```

### 7. 文件权限

确保应用有读写以下目录的权限:
- `uploads/` - 上传文件存储目录
- `downloads/` - 生成视频存储目录
- `tasks.json` - 任务状态存储文件

```bash
chmod 755 uploads downloads
chmod 644 tasks.json
```

### 8. 安全建议

1. 使用 HTTPS 加密传输
2. 限制上传文件大小和类型
3. 定期清理旧文件
4. 设置适当的防火墙规则
5. 使用非root用户运行应用

### 9. 故障排除

1. 如果遇到权限问题，请检查目录和文件权限
2. 如果API调用失败，请检查API密钥是否正确配置
3. 如果文件上传失败，请检查文件大小限制
4. 查看应用日志获取更多信息
5. 如果遇到 `ERR_CONTENT_LENGTH_MISMATCH` 错误:
   - 确保使用Nginx作为反向代理
   - 在Nginx配置中添加 `proxy_buffering off`
   - 检查文件传输是否使用流式处理
   - 确保Gunicorn和Nginx的超时设置足够长