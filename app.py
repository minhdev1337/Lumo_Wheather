from flask import Flask, render_template_string
import threading
import time
from datetime import datetime
import cv2
import numpy as np
import base64
import gc
import paramiko
from google import genai

app = Flask(__name__)

# ==========================================
# CẤU HÌNH HỆ THỐNG
# ==========================================
GEMINI_API_KEY = "AQ.Ab8RN6LD1ZVTFXwUKJJN0uvVCOxH7ySc9AqAGkoV7SZuaHhGjg"
RTSP_URL = "rtsp://admin:admin@frp.freefrp.net:35541/onvif1"
ROUTER_HOST = "frp.freefrp.net"
ROUTER_SSH_PORT = 32222
ROUTER_USER = "root"
ROUTER_PASS = "admin"

system_state = {
    "status": "Đang khởi động...",
    "brightness": 0,
    "lightning": False,
    "ram": "N/A",
    "cpu": "N/A",
    "logs": "Hệ thống khởi chạy thành công.\n",
    "frame_b64": "" # Chứa ảnh base64 siêu nhẹ
}

def add_log(msg):
    system_state["logs"] = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n" + system_state["logs"][:500] # Giới hạn log để tiết kiệm RAM

# ==========================================
# TIẾN TRÌNH AI (CHẠY NGẦM)
# ==========================================
def worker_loop():
    prev_brightness = None
    
    while True:
        try:
            # 1. Đọc SSH Router
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ROUTER_HOST, port=ROUTER_SSH_PORT, username=ROUTER_USER, password=ROUTER_PASS, timeout=5)
                _, stdout, _ = ssh.exec_command("free -m | grep Mem")
                ram_info = stdout.read().decode().split()
                system_state["ram"] = f"{ram_info[2]}/{ram_info[1]} MB"
                
                _, stdout, _ = ssh.exec_command("cat /proc/loadavg")
                system_state["cpu"] = stdout.read().decode().split()[0]
                ssh.close()
            except:
                pass # Bỏ qua lỗi để đỡ rác log

            # 2. Xử lý ảnh OpenCV ÉP XUNG RAM
            cap = cv2.VideoCapture(RTSP_URL)
            ret, frame = cap.read()
            cap.release()

            if ret and frame is not None:
                # ÉP CHẤT LƯỢNG: Thu nhỏ ảnh còn 320x240 để tiết kiệm RAM tuyệt đối
                frame = cv2.resize(frame, (320, 240))
                
                # Đo độ sáng
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                current_brightness = int(np.mean(hsv[:, :, 2]))
                system_state["brightness"] = current_brightness

                # Tính chớp/sét
                if prev_brightness is not None:
                    if (current_brightness - prev_brightness) > 60 and current_brightness > 150:
                        system_state["lightning"] = True
                        add_log("⚡ PHÁT HIỆN TIA CHỚP!")
                    else:
                        system_state["lightning"] = False
                prev_brightness = current_brightness

                # Encode ảnh ra base64 nhẹ nhất (Chất lượng 50%)
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                system_state["frame_b64"] = base64.b64encode(buffer).decode('utf-8')
            
            system_state["status"] = "Hoạt động bình thường"

        except Exception as e:
            system_state["status"] = f"Lỗi vòng lặp: {str(e)}"
        
        # Lệnh dọn rác thủ công (Ép giải phóng RAM chết)
        gc.collect() 
        time.sleep(5)

threading.Thread(target=worker_loop, daemon=True).start()

# ==========================================
# GIAO DIỆN WEB (FLASK) - Render HTML Tĩnh
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>AI Weather & Router</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="5"> <!-- Tự reload sau 5 giây -->
    <style>
        body { font-family: Arial; padding: 20px; background: #f4f4f9; }
        .card { background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px;}
        img { max-width: 100%; border-radius: 8px; }
        pre { background: #333; color: #fff; padding: 10px; border-radius: 5px; height: 150px; overflow-y: auto;}
    </style>
</head>
<body>
    <h2>🌩️ AI Weather Dashboard</h2>
    
    <div class="card">
        <h3>📷 Luồng Camera (320x240)</h3>
        {% if frame %}
            <img src="data:image/jpeg;base64,{{ frame }}">
        {% else %}
            <p>Đang tải camera...</p>
        {% endif %}
    </div>

    <div class="card">
        <h3>📊 Thông số Trạm</h3>
        <p><b>Trạng thái:</b> {{ status }}</p>
        <p><b>Độ sáng:</b> {{ brightness }}/255</p>
        <p><b>Phát hiện chớp:</b> {{ 'CÓ ⚡' if lightning else 'Không' }}</p>
    </div>

    <div class="card">
        <h3>🎛️ Router OpenWrt</h3>
        <p><b>RAM:</b> {{ ram }}</p>
        <p><b>CPU Load:</b> {{ cpu }}</p>
    </div>

    <div class="card">
        <h3>Nhật ký</h3>
        <pre>{{ logs }}</pre>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, 
                                  frame=system_state["frame_b64"],
                                  status=system_state["status"],
                                  brightness=system_state["brightness"],
                                  lightning=system_state["lightning"],
                                  ram=system_state["ram"],
                                  cpu=system_state["cpu"],
                                  logs=system_state["logs"])

if __name__ == '__main__':
    # Koyeb yêu cầu cổng mặc định là 8000
    app.run(host='0.0.0.0', port=8000)