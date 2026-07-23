import streamlit as st
import fitz  
from PIL import Image
import io
import google.generativeai as genai
import time

st.set_page_config(page_title="Hydraulic & Civil Review", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา")

# 1. ดึง API Key จาก Secrets ของ Streamlit
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
except:
    API_KEY = ""
    st.error("⚠️ ไม่พบ API Key กรุณาตั้งค่า GEMINI_API_KEY ในเมนู Secrets ของ Streamlit Cloud")

# 2. กำหนดโมเดลเป็นรุ่นมาตรฐานที่เสถียรที่สุดสำหรับบัญชีฟรี
try:
    # ล็อคชื่อโมเดลเป็น 1.5-flash เพื่อป้องกันการดึงรุ่นที่ติดสิทธิ์การใช้งาน
    model = genai.GenerativeModel('gemini-1.5-flash')
    st.success("✅ เชื่อมต่อ AI สำเร็จ! (ระบบเลือกใช้รุ่น: gemini-1.5-flash)")
except Exception as e:
    model = None
    st.error(f"❌ เกิดข้อผิดพลาดในการตั้งค่าโมเดล: {e}")
    
# 3. คำสั่งเฉพาะทางสำหรับวิศวกรรมชลประทาน
system_prompt = """
คุณคือวิศวกรโยธาและชลประทานระดับเชี่ยวชาญ กรุณาตรวจสอบรูปภาพแบบแปลนหรือรายการคำนวณที่แนบมาอย่างละเอียด:
1. วิเคราะห์และระบุว่านี่คืองานอะไร (เช่น แบบโครงสร้างอาคาร, แบบฝาย Labyrinth, รายการคำนวณความหนาคอนกรีต หรืออื่นๆ)
2. ตรวจสอบความถูกต้องทางวิศวกรรม เช่น ค่าแรงดันน้ำ (Uplift/Water Pressure), การหาค่า Factor of Safety ต่อการเลื่อนไถล
3. ตรวจสอบความสอดคล้องของตัวเลข (เช่น ระดับ Elevation) และหาคำผิด
4. สรุปข้อเสนอแนะและจุดที่ควรแก้ไขเป็นข้อๆ
"""

def analyze_image_with_ai(image_to_check):
    try:
        response = model.generate_content([system_prompt, image_to_check])
        return response.text
    except Exception as e:
        return f"เกิดข้อผิดพลาดในการวิเคราะห์: {e}"

uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลนหรือรายการคำนวณ", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None and model is not None:
    status_text = st.empty()
    status_text.info("⏳ กำลังใช้ AI อ่านไฟล์และวิเคราะห์ทางวิศวกรรม กรุณารอสักครู่...")
    
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    if file_extension in ["png", "jpg", "jpeg"]:
        image = Image.open(uploaded_file).convert("RGB")
        st.image(image, use_column_width=True)
        
        analysis_result = analyze_image_with_ai(image)
        
        status_text.empty()
        
        with st.expander("📝 ผลการตรวจสอบทางวิศวกรรมอย่างละเอียด", expanded=True):
            st.write(analysis_result)
            
    elif file_extension == "pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        for page_num in range(len(doc)):
            # ระบบหน่วงเวลาสำหรับ PDF เพื่อป้องกัน Error 429 (Quota Exceeded)
            if page_num > 0:
                cooldown_text = st.empty()
                cooldown_text.warning("⏳ รอ 15 วินาทีเพื่อป้องกันโควต้า API เต็ม...")
                time.sleep(15)
                cooldown_text.empty()
                
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            img_data = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_data)).convert("RGB")
            
            st.subheader(f"หน้า {page_num + 1}")
            st.image(image, use_column_width=True)
            
            analysis_result = analyze_image_with_ai(image)
            
            with st.expander(f"📝 ผลการตรวจสอบทางวิศวกรรม (หน้า {page_num + 1})", expanded=True):
                st.write(analysis_result)
        
        status_text.empty()
