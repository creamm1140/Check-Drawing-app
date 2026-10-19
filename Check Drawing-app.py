import streamlit as st
import fitz  # PyMuPDF
from PIL import Image, ImageDraw
import io
import json
import google.generativeai as genai
import time

st.set_page_config(page_title="Hydraulic & Civil Review Pro", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา (Pro)")

# 1. ตั้งค่า API Key และ Model
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
except:
    st.error("⚠️ ไม่พบ API Key กรุณาตั้งค่า GEMINI_API_KEY ในเมนู Secrets")

try:
    # แนะนำใช้รุ่น pro หากต้องการพิกัดที่แม่นยำขึ้น แต่ flash ก็สามารถทำงานได้
    model = genai.GenerativeModel('gemini-3.5-flash-lite')
except Exception as e:
    model = None
    st.error(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อโมเดล: {e}")

# 2. ปรับ System Prompt ให้บังคับตอบกลับเป็น JSON และขอพิกัด [ymin, xmin, ymax, xmax] 
system_prompt = """
คุณคือวิศวกรโยธาและชลประทานระดับเชี่ยวชาญ ตรวจสอบรูปภาพแบบแปลนหรือรายการคำนวณที่แนบมา
และคุณต้องตอบกลับมาในรูปแบบ JSON เท่านั้น โดยมีโครงสร้างดังนี้:
{
  "project_type": "ระบุว่านี่คืองานอะไร",
  "issues": [
    {
      "description": "รายละเอียดจุดที่ผิด หรือข้อเสนอแนะทางวิศวกรรม (เช่น Uplift Pressure, ค่า Elevation)",
      "box_2d": [ymin, xmin, ymax, xmax]
    }
  ],
  "summary": "สรุปข้อเสนอแนะรวม"
}

ข้อควรระวังสำหรับ box_2d (พิกัดจุดที่ผิด):
- ใช้สเกล 0 ถึง 1000 (โดยมุมซ้ายบนคือ 0,0 และมุมขวาล่างคือ 1000,1000)
- ymin: พิกัดแกน Y ด้านบน, xmin: พิกัดแกน X ด้านซ้าย, ymax: พิกัดแกน Y ด้านล่าง, xmax: พิกัดแกน X ด้านขวา
"""

def analyze_and_get_json(image_to_check):
    try:
        # บังคับให้ AI ตอบเป็น JSON
        response = model.generate_content(
            [system_prompt, image_to_check],
            generation_config=genai.GenerationConfig(response_mime_type="application/json")
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"เกิดข้อผิดพลาดในการวิเคราะห์หรือแปลง JSON: {e}")
        return None

def draw_on_pdf_page(page, issues):
    """วาดกรอบสีแดงลงบนหน้า PDF (fitz.Page) ตามพิกัดที่ AI วิเคราะห์มา"""
    rect_width = page.rect.width
    rect_height = page.rect.height
    
    for issue in issues:
        box = issue.get("box_2d", [0, 0, 0, 0])
        if box and len(box) == 4 and box != [0, 0, 0, 0]:
            ymin, xmin, ymax, xmax = box
            # แปลงสเกล 0-1000 กลับมาเป็นขนาดจริงของหน้า PDF
            x0 = (xmin / 1000.0) * rect_width
            y0 = (ymin / 1000.0) * rect_height
            x1 = (xmax / 1000.0) * rect_width
            y1 = (ymax / 1000.0) * rect_height
            
            # วาดกรอบสี่เหลี่ยมสีแดง (วาดแบบวงกลมอาจจะครอบคลุมตัวหนังสือยากกว่าสี่เหลี่ยม)
            rect = fitz.Rect(x0, y0, x1, y1)
            page.draw_rect(rect, color=(1, 0, 0), width=3) # สีแดง, เส้นหนา 3
            
            # ใส่หมายเลขหรือข้อความกำกับแบบย่อ (ออปชันเสริม)
            page.insert_text((x0, max(y0 - 5, 0)), "ISSUE!", color=(1, 0, 0), fontsize=12)

uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลนหรือรายการคำนวณ", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None and model is not None:
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    if st.button("🚀 เริ่มการวิเคราะห์"):
        status_text = st.empty()
        status_text.info("⏳ กำลังวิเคราะห์และมาร์กจุดที่ผิด กรุณารอสักครู่...")
        
        # --- กรณีเป็นไฟล์รูปภาพ ---
        if file_extension in ["png", "jpg", "jpeg"]:
            image = Image.open(uploaded_file).convert("RGB")
            
            result_json = analyze_and_get_json(image)
            if result_json:
                # วาดกรอบแดงบนรูปด้วย PIL
                draw = ImageDraw.Draw(image)
                img_width, img_height = image.size
                
                for issue in result_json.get("issues", []):
                    box = issue.get("box_2d", [0, 0, 0, 0])
                    if len(box) == 4 and box != [0, 0, 0, 0]:
                        ymin, xmin, ymax, xmax = box
                        x0, y0 = (xmin/1000)*img_width, (ymin/1000)*img_height
                        x1, y1 = (xmax/1000)*img_width, (ymax/1000)*img_height
                        draw.rectangle([x0, y0, x1, y1], outline="red", width=4)
                
                st.image(image, use_column_width=True)
                
                # แสดงผลการวิเคราะห์
                st.subheader(f"📌 ประเภทงาน: {result_json.get('project_type')}")
                for i, issue in enumerate(result_json.get('issues', [])):
                    st.error(f"**จุดที่ {i+1}:** {issue.get('description')}")
                st.success(f"**สรุปผล:** {result_json.get('summary')}")
                
                # สร้างปุ่มดาวน์โหลดรูปที่วงแดงแล้ว (แปลงเป็น PDF)
                pdf_bytes = io.BytesIO()
                image.save(pdf_bytes, format="PDF", resolution=100.0)
                st.download_button(
                    label="📥 ดาวน์โหลดไฟล์ภาพ (PDF) ที่ตรวจสอบแล้ว",
                    data=pdf_bytes.getvalue(),
                    file_name="reviewed_image.pdf",
                    mime="application/pdf"
                )

        # --- กรณีเป็นไฟล์ PDF ---
        elif file_extension == "pdf":
            doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
            
            for page_num in range(len(doc)):
                if page_num > 0:
                    time.sleep(15) # กัน Quota API เต็ม
                
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=150)
                img_data = pix.tobytes("png")
                image_for_ai = Image.open(io.BytesIO(img_data)).convert("RGB")
                
                result_json = analyze_and_get_json(image_for_ai)
                
                if result_json:
                    # นำผลวิเคราะห์ไปวาดกรอบแดงลงในเอกสาร doc ตัวเดิม
                    draw_on_pdf_page(page, result_json.get("issues", []))
                    
                    st.markdown(f"### 📄 หน้า {page_num + 1}: {result_json.get('project_type', '')}")
                    
                    # แปลงหน้า PDF ที่วาดแดงแล้วมาพรีวิวใน Streamlit
                    pix_annotated = page.get_pixmap(dpi=150)
                    img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                    st.image(img_annotated, use_column_width=True)
                    
                    # แสดงรายงานใต้ภาพ
                    with st.expander(f"📝 รายละเอียดการตรวจสอบ (หน้า {page_num + 1})", expanded=True):
                        for i, issue in enumerate(result_json.get('issues', [])):
                            st.error(f"**จุดที่ {i+1}:** {issue.get('description')}")
                        st.info(f"**สรุป:** {result_json.get('summary')}")

            # สิ้นสุดลูป PDF สร้างปุ่มดาวน์โหลด PDF ฉบับที่ถูกแก้แล้ว (มีวงกลม/กรอบแดง)
            out_pdf_bytes = io.BytesIO()
            doc.save(out_pdf_bytes)
            
            st.success("✅ การวิเคราะห์เสร็จสมบูรณ์!")
            st.download_button(
                label="📥 ดาวน์โหลดไฟล์ PDF ที่ถูกวงจุดที่ผิดแล้ว",
                data=out_pdf_bytes.getvalue(),
                file_name="reviewed_document.pdf",
                mime="application/pdf"
            )
            
        status_text.empty()
