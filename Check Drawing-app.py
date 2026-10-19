import streamlit as st
import fitz  # PyMuPDF
from PIL import Image, ImageDraw
import io
import json
import gc  # ไลบรารีสำหรับคืนพื้นที่ RAM
import google.generativeai as genai
import time

st.set_page_config(page_title="Hydraulic & Civil Review Pro", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา (Pro)")

# 1. ตั้งค่า API Key และ Model
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    # ใช้รุ่นที่เสถียรและรองรับ API ของคุณ
    model = genai.GenerativeModel('gemini-3.5-flash-lite') 
except Exception as e:
    model = None
    st.error(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ: {e}")

# 2. System Prompt ที่ครอบคลุมทุกประเด็น
system_prompt = """
คุณคือวิศวกรโยธาและชลประทานระดับเชี่ยวชาญ เป็นผู้ตรวจแบบ (Reviewer) ที่ละเอียดรอบคอบ 
ฉันจะส่งรูปภาพแบบแปลนหรือรายการคำนวณให้คุณ (อาจมีหลายหน้า)
หน้าที่ของคุณคือตรวจสอบอย่างละเอียดตามหลักเกณฑ์นี้:

1. ประเภทงาน: ระบุภาพรวมว่านี่คืองานอะไร
2. ความถูกต้อง: ตรวจสอบค่าแรงดันน้ำ (Uplift Pressure), Factor of Safety, ความหนาคอนกรีต, ฯลฯ
3. ความสอดคล้อง (Cross-Reference): เปรียบเทียบความสอดคล้องของตัวเลข (เช่น ระดับ Elevation) ระหว่างหน้าแบบแปลนกับตารางคำนวณ ว่าขัดแย้งกันหรือไม่
4. การสะกดคำ: หาคำที่สะกดผิด ทั้งภาษาไทยและศัพท์เทคนิคภาษาอังกฤษ (เช่น Uplift)

คุณต้องตอบกลับมาในรูปแบบ JSON เท่านั้น โครงสร้างดังนี้:
{
  "project_type": "ประเภทของโครงการ/งานที่ตรวจพบ",
  "issues": [
    {
      "page_index": [ระบุ index ของหน้าที่พบปัญหา เริ่มที่ 0 (ถ้ามีหน้าเดียวใส่ 0)],
      "description": "อธิบายปัญหา เช่น 'ระดับสันฝายหน้า 1 ไม่ตรงกับรายการคำนวณหน้า 2' หรือ 'สะกดคำผิด'",
      "box_2d": [ymin, xmin, ymax, xmax] 
    }
  ],
  "summary": "สรุปผลการตรวจสอบและข้อเสนอแนะรวม"
}

กฎพิกัด box_2d:
- ใช้สเกล 0 ถึง 1000 สำหรับรูปภาพในหน้านั้นๆ (มุมซ้ายบนคือ 0,0 ขวาล่างคือ 1000,1000)
- ymin: บน, xmin: ซ้าย, ymax: ล่าง, xmax: ขวา
- หากหาพิกัดไม่ได้ หรือเป็นข้อสังเกตภาพรวม ให้ส่ง [0, 0, 0, 0]
"""

def clean_json_response(text):
    """ฟังก์ชันเสริมสำหรับลบเครื่องหมาย Markdown เผื่อ AI ตอบกลับมาไม่เป็น JSON ล้วน"""
    try:
        cleaned = text.replace('```json', '').replace('```', '').strip()
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None

def draw_on_pdf_page(page, issues, current_page_index):
    """วาดกรอบสีแดงเฉพาะจุดที่พบปัญหาในหน้านั้นๆ"""
    rect_width = page.rect.width
    rect_height = page.rect.height
    
    for issue in issues:
        if issue.get("page_index", 0) == current_page_index:
            box = issue.get("box_2d", [0, 0, 0, 0])
            if box and len(box) == 4 and box != [0, 0, 0, 0]:
                ymin, xmin, ymax, xmax = box
                x0 = (xmin / 1000.0) * rect_width
                y0 = (ymin / 1000.0) * rect_height
                x1 = (xmax / 1000.0) * rect_width
                y1 = (ymax / 1000.0) * rect_height
                
                rect = fitz.Rect(x0, y0, x1, y1)
                page.draw_rect(rect, color=(1, 0, 0), width=3)

uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลนหรือรายการคำนวณ", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None and model is not None:
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    if st.button("🚀 เริ่มการวิเคราะห์ (ตรวจสอบความสอดคล้อง)"):
        status_text = st.empty()
        status_text.info("⏳ กำลังเตรียมไฟล์... (ระบบกำลังบริหารจัดการ RAM)")
        
        # --- กรณีเป็นไฟล์รูปภาพ (มีหน้าเดียว) ---
        if file_extension in ["png", "jpg", "jpeg"]:
            image = Image.open(uploaded_file).convert("RGB")
            try:
                response = model.generate_content(
                    [system_prompt, image],
                    generation_config=genai.GenerationConfig(response_mime_type="application/json")
                )
                
                result_json = clean_json_response(response.text)
                
                if result_json:
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
                    st.subheader(f"📌 โครงการ: {result_json.get('project_type')}")
                    for i, issue in enumerate(result_json.get('issues', [])):
                        st.error(f"**จุดที่ {i+1}:** {issue.get('description')}")
                    
                    pdf_bytes = io.BytesIO()
                    image.save(pdf_bytes, format="PDF", resolution=100.0)
                    st.download_button("📥 ดาวน์โหลดไฟล์ (PDF)", data=pdf_bytes.getvalue(), file_name="reviewed.pdf", mime="application/pdf")
                    status_text.success("✅ เสร็จสิ้น")
                else:
                    status_text.error("❌ AI ไม่สามารถประมวลผลรูปแบบ JSON ได้ ลองใหม่อีกครั้ง")
            except Exception as e:
                status_text.error(f"❌ เกิดข้อผิดพลาดจาก API: {e}")

        # --- กรณีเป็นไฟล์ PDF (ตรวจหลายหน้าพร้อมกัน) ---
        elif file_extension == "pdf":
            try:
                doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
                images_to_send = []
                
                # [ป้องกัน OOM Crash] ปรับ DPI อัตโนมัติ: ถ้ายิ่งหลายหน้า ยิ่งต้องลดความละเอียดเพื่อประหยัด RAM
                current_dpi = 100 if len(doc) <= 5 else 72
                
                status_text.info(f"⏳ กำลังแปลง PDF ทั้ง {len(doc)} หน้า (DPI: {current_dpi})...")
                
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=current_dpi)
                    img_data = pix.tobytes("png")
                    img = Image.open(io.BytesIO(img_data)).convert("RGB")
                    images_to_send.append(img)
                    
                    # เคลียร์ตัวแปรทิ้งและสั่งล้าง RAM ทันที
                    del pix
                    del img_data
                    gc.collect() 

                status_text.info("⏳ กำลังให้ AI ตรวจสอบและเปรียบเทียบข้อมูลข้ามหน้า อาจใช้เวลา 30-60 วินาที...")
                prompt_content = [system_prompt] + images_to_send
                
                response = model.generate_content(
                    prompt_content,
                    generation_config=genai.GenerationConfig(response_mime_type="application/json")
                )
                
                result_json = clean_json_response(response.text)
                
                if result_json:
                    st.subheader(f"📌 ภาพรวมโครงการ: {result_json.get('project_type', 'ไม่ระบุ')}")
                    st.info(f"**สรุปข้อเสนอแนะรวม:** {result_json.get('summary', '')}")
                    
                    for page_num in range(len(doc)):
                        page = doc.load_page(page_num)
                        draw_on_pdf_page(page, result_json.get("issues", []), page_num)
                        
                        st.markdown(f"### 📄 หน้า {page_num + 1}")
                        pix_annotated = page.get_pixmap(dpi=120) # พรีวิวหน้าเว็บให้ชัดขึ้นได้ เพราะหน้าเดียว
                        img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                        st.image(img_annotated, use_column_width=True)
                        
                        issues_this_page = [i for i in result_json.get("issues", []) if i.get("page_index", -1) == page_num]
                        if issues_this_page:
                            with st.expander(f"📝 จุดที่ต้องแก้ไข (พบ {len(issues_this_page)} จุด)", expanded=True):
                                for idx, issue in enumerate(issues_this_page):
                                    st.error(f"**- {issue.get('description')}**")
                        else:
                            st.success("✅ ไม่พบข้อผิดพลาดที่ชัดเจนในหน้านี้")
                        st.markdown("---")

                    out_pdf_bytes = io.BytesIO()
                    doc.save(out_pdf_bytes)
                    status_text.success("✅ การวิเคราะห์เสร็จสมบูรณ์!")
                    
                    st.download_button(
                        label="📥 ดาวน์โหลดไฟล์ PDF สรุปผลตรวจสอบ (วงกลมแดง)",
                        data=out_pdf_bytes.getvalue(),
                        file_name="reviewed_document_pro.pdf",
                        mime="application/pdf"
                    )
                else:
                    status_text.error("❌ การแปลงผลลัพธ์ผิดพลาด กรุณาลองใหม่อีกครั้ง")
            
            except Exception as e:
                status_text.error(f"❌ เกิดข้อผิดพลาดระหว่างส่งข้อมูล: {e}")
