import streamlit as st
import fitz  
from PIL import Image
import io
import google.generativeai as genai

# ---------------------------------------------------------
# นำ API Key ที่ได้จาก Google AI Studio มาใส่ในเครื่องหมายคำพูดด้านล่าง
API_KEY = "AQ.Ab8RN6JdDO3_4px0LGdDz5uo8cfTlK_5dKSFK1q8_R0QIdbs5A"
# ---------------------------------------------------------

st.set_page_config(page_title="Hydraulic & Civil Review", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา")
st.markdown("อัปโหลดไฟล์ (PDF, PNG, JPG) ระบบจะใช้ AI อ่านเนื้อหาในแบบและวิเคราะห์ทางวิศวกรรมโดยละเอียด")

# ตั้งค่า AI
if API_KEY != "ใส่_API_KEY_ของคุณที่นี่":
    genai.configure(api_key=API_KEY)
    # ใช้โมเดล Gemini 1.5 Flash หรือ Pro ที่รองรับการอ่านรูปภาพ
    model = genai.GenerativeModel('gemini-1.5-pro')
else:
    st.warning("⚠️ กรุณาใส่ API Key ในโค้ดก่อนเพื่อให้ AI ทำงานได้จริง")

uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลนหรือรายการคำนวณ", type=["pdf", "png", "jpg", "jpeg"])

# Prompt (คำสั่ง) ที่เราจะบังคับให้ AI ทำหน้าที่เป็นวิศวกรโยธา/ชลประทาน
system_prompt = """
คุณคือวิศวกรโยธาและชลประทานระดับเชี่ยวชาญ กรุณาตรวจสอบรูปภาพแบบแปลนหรือรายการคำนวณที่แนบมาอย่างละเอียด:
1. วิเคราะห์และระบุว่านี่คืองานอะไร (เช่น อาคาร, ฝาย Labyrinth, โครงสร้างทางชลศาสตร์ หรืออื่นๆ)
2. ตรวจสอบความถูกต้องทางวิศวกรรม เช่น ความหนาของคอนกรีต, แรงดันน้ำ (Uplift/Water Pressure), การหาค่า Factor of Safety ต่อการเลื่อนไถล หรือสัดส่วนของโครงสร้าง
3. ตรวจสอบความสอดคล้องของตัวเลข (เช่น ระดับ Elevation) และคำผิดในแบบ
4. สรุปข้อเสนอแนะและจุดที่ควรแก้ไขเป็นข้อๆ อย่างชัดเจน
"""

def analyze_image_with_ai(image):
    if API_KEY == "ใส่_API_KEY_ของคุณที่นี่":
        return "ระบบยังไม่ได้เชื่อมต่อ API Key ไม่สามารถวิเคราะห์แบบได้"
    try:
        response = model.generate_content([system_prompt, image])
        return response.text
    except Exception as e:
        return f"เกิดข้อผิดพลาดในการวิเคราะห์: {e}"

if uploaded_file is not None:
    st.info("กำลังใช้ AI อ่านไฟล์และวิเคราะห์ทางวิศวกรรม กรุณารอสักครู่...")
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    # กรณี: ไฟล์รูปภาพ
    if file_extension in ["png", "jpg", "jpeg"]:
        image = Image.open(uploaded_file).convert("RGB")
        st.subheader("แบบที่อัปโหลด")
        st.image(image, use_column_width=True)
        
        with st.spinner('AI กำลังตรวจแบบ...'):
            analysis_result = analyze_image_with_ai(image)
            
        with st.expander("📝 ผลการตรวจสอบทางวิศวกรรมอย่างละเอียด", expanded=True):
            st.write(analysis_result)
            
    # กรณี: ไฟล์ PDF
    elif file_extension == "pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            img_data = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_data)).convert("RGB")
            
            st.subheader(f"หน้า {page_num + 1}")
            st.image(image, use_column_width=True)
            
            with st.spinner(f'AI กำลังตรวจแบบหน้าที่ {page_num + 1}...'):
                analysis_result = analyze_image_with_ai(image)
                
            with st.expander(f"📝 ผลการตรวจสอบทางวิศวกรรม (หน้า {page_num + 1})", expanded=True):
                st.write(analysis_result)
