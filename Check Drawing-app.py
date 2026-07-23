import streamlit as st
import fitz  
from PIL import Image
import io
import google.generativeai as genai

st.set_page_config(page_title="Hydraulic & Civil Review", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา")

# 1. ดึง API Key จาก Secrets ของ Streamlit
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
except:
    API_KEY = ""
    st.error("⚠️ ไม่พบ API Key กรุณาตั้งค่า GEMINI_API_KEY ในเมนู Secrets ของ Streamlit Cloud")

# 2. ฟังก์ชันค้นหาและเลือกโมเดลที่ใช้งานได้จริงโดยอัตโนมัติ
@st.cache_resource
def get_valid_model():
    if not API_KEY:
        return None, "No API Key"
    try:
        available_models = []
        # ค้นหาโมเดลทั้งหมดที่รองรับการอ่านภาพ (generateContent)
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        target_model = None
        # พยายามเลือกรุ่น Pro หรือ Flash ที่ใหม่ที่สุดที่มีในระบบ
        for name in available_models:
            if 'pro' in name:
                target_model = name
                break
        if not target_model:
            for name in available_models:
                if 'flash' in name:
                    target_model = name
                    break
        
        # ถ้าหาไม่เจอเลย ให้ดึงตัวแรกสุดที่ระบบมี
        if not target_model and available_models:
            target_model = available_models[0]
            
        return genai.GenerativeModel(target_model), target_model
    except Exception as e:
        return None, str(e)

model, model_name = get_valid_model()

if model:
    st.success(f"✅ เชื่อมต่อ AI สำเร็จ! (ระบบเลือกใช้รุ่น: {model_name})")
    
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
    # สร้างกล่องข้อความจำเพาะที่สามารถสั่งลบตัวเองได้
    status_text = st.empty()
    status_text.info("⏳ กำลังใช้ AI อ่านไฟล์และวิเคราะห์ทางวิศวกรรม กรุณารอสักครู่...")
    
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    if file_extension in ["png", "jpg", "jpeg"]:
        image = Image.open(uploaded_file).convert("RGB")
        st.image(image, use_column_width=True)
        
        analysis_result = analyze_image_with_ai(image)
        
        # ลบข้อความ "กำลังโหลด..." ทิ้ง
        status_text.empty()
        
        with st.expander("📝 ผลการตรวจสอบทางวิศวกรรมอย่างละเอียด", expanded=True):
            st.write(analysis_result)
            
    elif file_extension == "pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            img_data = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_data)).convert("RGB")
            
            st.subheader(f"หน้า {page_num + 1}")
            st.image(image, use_column_width=True)
            
            analysis_result = analyze_image_with_ai(image)
            
            with st.expander(f"📝 ผลการตรวจสอบทางวิศวกรรม (หน้า {page_num + 1})", expanded=True):
                st.write(analysis_result)
        
        # ลบข้อความ "กำลังโหลด..." ทิ้งเมื่อวนลูปครบทุกหน้า
        status_text.empty()
