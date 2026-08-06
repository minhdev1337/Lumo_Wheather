import os
import vlc
import time
import cv2
import requests
import numpy as np
import json
import base64
import socket
from urllib.parse import urlparse
from datetime import datetime
from google import genai
from google.genai import types

# ==========================================
# CẤU HÌNH HỆ THỐNG & API KẾT NỐI
# ==========================================
RTSP_URL = os.environ.get("RTSP_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Tọa độ chính xác: 10°20'14.4"N 107°04'49.4"E
VT_LAT = "10.3373"
VT_LON = "107.0804"

# Chìa khóa của 2 API
WEATHERAPI_KEY = os.environ.get("WEATHER_API_KEY")
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY")

MOUNTAIN_ROI = (100, 150, 200, 200) 
SNAPSHOT_FILE = os.path.abspath("vlc_snapshot.jpg")

def ptz_control(action):
    pass 

def apply_clahe(image):
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    cl = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((cl, a, b)), cv2.COLOR_LAB2BGR)

def measure_mountain_sharpness(frame):
    x, y, w, h = MOUNTAIN_ROI
    roi = frame[y:y+h, x:x+w]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    brightness = np.mean(gray)
    return sharpness, brightness

def check_remote_radar():
    lightning_warning = False
    api_rain_count = 0
    
    # 1. OPEN-METEO
    try:
        url_om = f"https://api.open-meteo.com/v1/forecast?latitude={VT_LAT}&longitude={VT_LON}&current=precipitation,weather_code"
        res_om = requests.get(url_om, timeout=5).json()
        if res_om.get("current", {}).get("precipitation", 0) > 0 or res_om.get("current", {}).get("weather_code", 0) >= 51:
            api_rain_count += 1
        if res_om.get("current", {}).get("weather_code", 0) in [95, 96, 99]:
            lightning_warning = True
    except Exception as e:
        print("Lỗi Open-Meteo:", e)

    # 2. WEATHERAPI
    try:
        if WEATHERAPI_KEY:
            url_wapi = f"http://api.weatherapi.com/v1/current.json?key={WEATHERAPI_KEY}&q={VT_LAT},{VT_LON}"
            res_wapi = requests.get(url_wapi, timeout=5).json()
            condition = res_wapi.get("current", {}).get("condition", {}).get("text", "").lower()
            if "rain" in condition or "drizzle" in condition or "thunder" in condition:
                api_rain_count += 1
            if "thunder" in condition:
                lightning_warning = True
    except Exception as e:
        print("Lỗi WeatherAPI:", e)

    # 3. OPENWEATHERMAP
    try:
        if OPENWEATHER_API_KEY:
            url_owm = f"https://api.openweathermap.org/data/2.5/weather?lat={VT_LAT}&lon={VT_LON}&appid={OPENWEATHER_API_KEY}"
            res_owm = requests.get(url_owm, timeout=5).json()
            weather_id = res_owm.get("weather", [{}])[0].get("id", 800)
            if weather_id < 700: 
                api_rain_count += 1
            if 200 <= weather_id < 300: 
                lightning_warning = True
    except Exception as e:
        print("Lỗi OpenWeatherMap:", e)
        
    return lightning_warning, api_rain_count

def analyze_sky_with_gemini(image_path):
    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    zenith = img[0:int(h/2), :]
    horizon = img[int(h/2):h, :]
    
    cv2.imwrite("zenith.jpg", zenith)
    cv2.imwrite("horizon.jpg", horizon)
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = """
    Tôi có 2 ảnh bầu trời: Ảnh 1 (Đỉnh trời), Ảnh 2 (Chân trời).
    Kiểm tra:
    1. Có mây vũ tích khổng lồ không?
    2. Chân trời còn sáng hay đã bị màn mây/mưa che kín?
    3. Có 'dải mưa' (rain shafts) trút xuống ở phía xa không?
    Dựa trên quan sát, hãy chốt 1 cụm từ duy nhất: QUANG ĐÃNG, MÂY BAY NGANG, hoặc SẮP MƯA DÔNG.
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[prompt, 
                      types.Part.from_bytes(data=open("zenith.jpg", "rb").read(), mime_type='image/jpeg'), 
                      types.Part.from_bytes(data=open("horizon.jpg", "rb").read(), mime_type='image/jpeg')]
        )
        return response.text.strip().upper()
    except Exception as e:
        print("Lỗi gọi Gemini:", e)
        return "LỖI AI"

def capture_frame_via_vlc():
    """Hàm chuyên dụng sử dụng nhân VLC để vượt qua tường lửa TCP/FRP và chụp khung hình"""
    if os.path.exists(SNAPSHOT_FILE):
        os.remove(SNAPSHOT_FILE)

    vlc_instance = vlc.Instance("--vout=dummy", "--aout=dummy", "--quiet")
    player = vlc_instance.media_player_new()
    media = vlc_instance.media_new(RTSP_URL)
    
    media.add_option(":rtsp-tcp")
    media.add_option(":network-caching=5000")
    media.add_option(":no-audio")             
    
    player.set_media(media)
    player.play()
    time.sleep(8) 

    success = False
    if player.is_playing():
        for _ in range(5):
            player.video_take_snapshot(0, SNAPSHOT_FILE, 0, 0)
            time.sleep(1.5)
            if os.path.exists(SNAPSHOT_FILE) and os.path.getsize(SNAPSHOT_FILE) > 0:
                success = True
                break
    
    player.stop()

    if success:
        frame = cv2.imread(SNAPSHOT_FILE)
        return frame
    return None

def check_frp_network(rtsp_url, timeout=3):
    """Kiểm tra xem máy chủ GitHub có bị FRP chặn IP không"""
    print("⏳ Đang kiểm tra cổng mạng từ GitHub đến FRP...")
    try:
        if not rtsp_url:
            print("❌ LỖI: Đường dẫn RTSP_URL trống hoặc không tồn tại.")
            return False
            
        parsed = urlparse(rtsp_url.replace("rtsp://", "http://"))
        host = parsed.hostname
        port = parsed.port or 554
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        print("✅ Mạng thông suốt! GitHub không bị chặn IP.")
        return True
    except socket.timeout:
        print("❌ LỖI MẠNG: Kết nối bị treo! Máy chủ GitHub đã bị chặn IP (Blackholed).")
        return False
    except Exception as e:
        print(f"❌ LỖI MẠNG: Không thể kết nối tới máy chủ Camera ({e})")
        return False

# ==========================================
# LUỒNG XỬ LÝ TRUNG TÂM
# ==========================================
def main():
    fallback_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": 1,
        "status": "Mất kết nối Camera hoặc luồng đệm mạng quá chậm. Đang chờ quét lại...",
        "image_b64": ""
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(fallback_data, f, ensure_ascii=False, indent=4)

    # Bắt buộc check mạng trước tiên để tránh treo quy trình GitHub
    if not check_frp_network(RTSP_URL):
        print("Dừng quy trình vì hệ thống mạng bị nghẽn hoặc bị chặn IP.")
        return

    alert_level = 1
    alert_msg = "Trời quang mây tạnh."
    
    frame = capture_frame_via_vlc()
    if frame is None:
        print("Không thể trích xuất khung hình từ Camera qua VLC. Đã xuất JSON dự phòng.")
        return

    frame = apply_clahe(cv2.resize(frame, (640, 480)))
    
    sharpness, brightness = measure_mountain_sharpness(frame)
    if brightness < 100 and sharpness < 50:
        alert_level = 3
        alert_msg = "CẢNH BÁO ĐỎ (CỤC BỘ): Màn mưa đã che khuất ngọn núi. Mưa cực lớn đang ập tới!"
    else:
        lightning_warning, api_rain = check_remote_radar()
        
        if lightning_warning or api_rain >= 2:
            ptz_control('UP_CENTER')
            cv2.imwrite("sky.jpg", frame) 
            ptz_control('DOWN_DEFAULT')
            
            ai_verdict = analyze_sky_with_gemini("sky.jpg")
            
            if "SẮP MƯA DÔNG" in ai_verdict:
                alert_level = 3
                msg_parts = [f"CẢNH BÁO ĐỎ: AI phát hiện mây dông! (Đồng thuận API: {api_rain}/3)"]
                if lightning_warning: msg_parts.append("Đài radar cảnh báo có sấm sét rất gần.")
                alert_msg = " ".join(msg_parts)
            else:
                alert_level = 2
                alert_msg = f"THEO DÕI: API báo có biến động ({api_rain}/3 trạm), nhưng AI xác nhận chân trời vẫn đang quang."

    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
    data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": alert_level,
        "status": alert_msg,
        "image_b64": base64.b64encode(buffer).decode('utf-8')
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print("Hoàn tất quy trình. Đã xuất JSON thành công.")

if __name__ == "__main__":
    main()
