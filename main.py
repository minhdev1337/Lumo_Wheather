import os
import cv2
import requests
import numpy as np
import json
import base64
from datetime import datetime
from google import genai
from google.genai import types

# ==========================================
# CẤU HÌNH HỆ THỐNG & API
# ==========================================
RTSP_URL = os.environ.get("RTSP_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY") # API Key của dịch vụ thời tiết (VD: WeatherAPI.com)

# Tọa độ ngọn núi để làm "Kill-Switch" (Cần điều chỉnh x, y, w, h cho khớp thực tế)
MOUNTAIN_ROI = (100, 150, 200, 200) 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message})

def ptz_control(action):
    # Hàm bắn gói tin ONVIF/CGI qua FRPC để điều khiển PTZ
    # Ví dụ: requests.get("http://frp.freefrp.net:38081/cgi-bin/ptz.cgi?action=" + action)
    pass 

def apply_clahe(image):
    # Khử lóa sáng trắng bằng không gian màu LAB
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    cl = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((cl, a, b)), cv2.COLOR_LAB2BGR)

def measure_mountain_sharpness(frame):
    # Đo độ nét của ngọn núi bằng phương sai Laplacian
    x, y, w, h = MOUNTAIN_ROI
    roi = frame[y:y+h, x:x+w]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    brightness = np.mean(gray)
    return sharpness, brightness

def check_remote_radar():
    """
    Sử dụng API (VD: WeatherAPI) để quét radar sấm sét và áp suất từ xa.
    Trả về: Có cảnh báo sét gần không, Số lượng API báo mưa.
    """
    lightning_warning = False
    api_rain_count = 0
    
    try:
        # Ví dụ gọi WeatherAPI để lấy dữ liệu Nowcast
        # url = f"http://api.weatherapi.com/v1/current.json?key={WEATHER_API_KEY}&q=Tọa_độ_của_bạn"
        # data = requests.get(url).json()
        
        # Giả lập logic phân tích JSON từ API:
        # 1. Kiểm tra mã thời tiết (Weather Code) có báo dông sét không
        # 2. Kiểm tra cảnh báo (Alerts) trong bán kính 10km
        
        # Mô phỏng dữ liệu trả về cho logic
        lightning_warning = True  # Phát hiện sét cách 5km
        api_rain_count = 2        # 2/3 hệ thống dự báo có mưa
        
    except Exception as e:
        print(f"Lỗi gọi API Thời tiết: {e}")
        
    return lightning_warning, api_rain_count

def analyze_sky_with_gemini(image_path):
    # Cắt ảnh để Gemini không bị nhầm lẫn giữa đỉnh đầu và chân trời
    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    zenith = img[0:int(h/2), :]
    horizon = img[int(h/2):h, :]
    
    cv2.imwrite("zenith.jpg", zenith)
    cv2.imwrite("horizon.jpg", horizon)
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = """
    Tôi có 2 ảnh: Ảnh 1 (Đỉnh trời), Ảnh 2 (Chân trời).
    Kiểm tra:
    1. Có mây vũ tích khổng lồ không?
    2. Chân trời còn sáng hay đã bị che kín?
    3. Có 'dải mưa' (rain shafts) phía xa không?
    Trả lời ngắn gọn trạng thái: QUANG ĐÃNG, MÂY BAY NGANG, hay SẮP MƯA DÔNG.
    """
    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=[prompt, 
                      types.Part.from_bytes(data=open("zenith.jpg", "rb").read(), mime_type='image/jpeg'), 
                      types.Part.from_bytes(data=open("horizon.jpg", "rb").read(), mime_type='image/jpeg')]
        )
        return response.text.strip().upper()
    except:
        return "LỖI AI"

# ==========================================
# LUỒNG XỬ LÝ TRUNG TÂM
# ==========================================
def main():
    alert_level = 1
    alert_msg = "Trời quang mây tạnh."
    
    # 1. Lấy ảnh gốc ngó sân & núi
    cap = cv2.VideoCapture(RTSP_URL)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return

    frame = apply_clahe(cv2.resize(frame, (640, 480)))
    
    # 2. KIỂM TRA ĐỘT XUẤT (Microclimate - Ngọn núi)
    sharpness, brightness = measure_mountain_sharpness(frame)
    if brightness < 100 and sharpness < 50:
        alert_level = 3
        alert_msg = "CẢNH BÁO ĐỎ (CỤC BỘ): Màn mưa đã che khuất ngọn núi. Mưa cực lớn đang ập tới!"
        send_telegram(alert_msg)
    else:
        # 3. QUÉT RADAR TỪ XA
        lightning_warning, api_rain = check_remote_radar()
        
        # Nếu Radar có tín hiệu -> Đánh thức Camera quét trời
        if lightning_warning or api_rain >= 2:
            ptz_control('UP_CENTER')
            # Lưu ảnh bầu trời (Giả lập)
            cv2.imwrite("sky.jpg", frame) 
            ptz_control('DOWN_DEFAULT')
            
            ai_verdict = analyze_sky_with_gemini("sky.jpg")
            
            # 4. MA TRẬN QUYẾT ĐỊNH
            if "SẮP MƯA DÔNG" in ai_verdict:
                alert_level = 3
                msg_parts = ["CẢNH BÁO ĐỎ: AI phát hiện mây vũ tích!"]
                if lightning_warning: msg_parts.append("Radar báo có sấm sét quanh khu vực.")
                alert_msg = " ".join(msg_parts)
                send_telegram(alert_msg)
            else:
                alert_level = 2
                alert_msg = f"THEO DÕI: Radar cảnh báo sấm sét từ xa, nhưng AI xác nhận chân trời vẫn quang."
                # Không báo Telegram để tránh spam, chỉ lưu trạng thái lên Web

    # 5. XUẤT BÁO CÁO LÊN WEB (Chuyển B64)
    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
    data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": alert_level,
        "status": alert_msg,
        "image_b64": base64.b64encode(buffer).decode('utf-8')
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main()
