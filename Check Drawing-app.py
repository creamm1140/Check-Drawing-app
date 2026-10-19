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
    # ใช้รุ่นที่รองรับ Context ยาวๆ ได้ดี และวิเคราะห์ภาพได้หลายภาพ (Gemini 1.5 Pro จะดีที่สุดสำหรับงานนี้ แต่ Flash ก็ทำได้)
    model = genai.GenerativeModel('gemini-1.5-flash') 
    st.success("✅ เชื่อมต่อ AI สำเร็จ!")
except Exception as e:
    model = None
    st.error(f"❌ เกิดข้อผิดพลาดในการตั้งค่าโมเดล: {e}")

# 2. System Prompt ที่ครอบคลุมทุกประเด็น
system_prompt = """
คุณคือวิศวกรโยธาและชลประทานระดับเชี่ยวชาญ และเป็นผู้ตรวจแบบ (Reviewer) ที่ละเอียดรอบคอบ 
ฉันจะส่งรูปภาพแบบแปลนและ/หรือรายการคำนวณให้คุณ (อาจมีหลายหน้า)
หน้าที่ของคุณคือตรวจสอบอย่างละเอียดตามหลักเกณฑ์ต่อไปนี้:

1. ประเภทงาน: วิเคราะห์และระบุภาพรวมว่านี่คืองานอะไร
2. ความถูกต้องทางวิศวกรรม: ตรวจสอบค่าต่างๆ เช่น แรงดันน้ำ (Uplift/Water Pressure), Factor of Safety, ความหนาคอนกรีต, การกระจายน้ำหนัก
3. ความสอดคล้องข้ามหน้า (Cross-Reference): **สำคัญมาก** หากได้รับภาพหลายหน้า ให้เปรียบเทียบความสอดคล้องของตัวเลข (เช่น ระดับ Elevation, ขนาดมิติ) ระหว่างหน้าแบบแปลนกับหน้าตารางคำนวณ หรือรูปด้านกับรูปตัด ว่าขัดแย้งกันหรือไม่
4. การสะกดคำผิด: ค้นหาและระบุคำที่สะกดผิด ทั้งภาษาไทยและศัพท์เทคนิคภาษาอังกฤษ (เช่น Uplift, Coefficient)

การตอบกลับ:
คุณต้องตอบกลับมาในรูปแบบ JSON เท่านั้น โครงสร้างดังนี้:
{
  "project_type": "ประเภทของโครงการ/งานที่ตรวจพบ",
  "issues": [
    {
      "page_index": [ระบุ index ของหน้าที่พบปัญหา เริ่มที่ 0 (ถ้ามีหน้าเดียวใส่ 0)],
      "description": "อธิบายปัญหาที่พบ เช่น 'ระดับสันฝายในแบบหน้า 1 (El. +10.00) ไม่ตรงกับรายการคำนวณหน้า 2 (El. +10.50)' หรือ 'สะกดคำว่า Uplift ผิดเป็น Uplif'",
      "box_2d": [ymin, xmin, ymax, xmax] 
    }
  ],
  "summary": "สรุปผลการตรวจสอบและข้อเสนอแนะรวม"
}

กฎของการให้พิกัด box_2d:
- ใช้สเกล 0 ถึง 1000 สำหรับรูปภาพในหน้านั้นๆ (มุมซ้ายบนคือ 0,0 ขวาล่างคือ 1000,1000)
- ymin: บน, xmin: ซ้าย, ymax: ล่าง, xmax: ขวา
- หากไม่สามารถระบุพิกัดที่ชัดเจนได้ให้ส่ง [0, 0, 0, 0]
"""

def draw_on_pdf_page(page, issues, current_page_index):
    """วาดกรอบสีแดงเฉพาะ issue ที่ตรงกับหน้าปัจจุบัน"""
    rect_width = page.rect.width
    rect_height = page.rect.height
    
    for issue in issues:
        # เช็คว่า issue นี้เป็นของหน้านี้หรือไม่
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
                # เพิ่มไฮไลท์แบบโปร่งแสงเล็กน้อย
                annot = page.add_highlight_annot(rect)
                annot.update()

uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลนหรือรายการคำนวณ", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None and model is not None:
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    if st.button("🚀 เริ่มการวิเคราะห์ (ตรวจสอบความสอดคล้อง)"):
        status_text = st.empty()
        status_text.info("⏳ กำลังเตรียมไฟล์และส่งให้ AI วิเคราะห์ อาจใช้เวลาสักครู่ (โดยเฉพาะ PDF หลายหน้า)...")
        
        # --- กรณีเป็นไฟล์รูปภาพ (มีหน้าเดียว) ---
        if file_extension in ["png", "jpg", "jpeg"]:
            image = Image.open(uploaded_file).convert("RGB")
            
            try:
                # ส่งภาพพร้อม Prompt
                response = model.generate_content(
                    [system_prompt, image],
                    generation_config=genai.GenerationConfig(response_mime_type="application/json")
                )
                result_json = json.loads(response.text)
                
                # ... (ส่วนการวาดรูป PIL เหมือนโค้ดเวอร์ชันก่อนหน้า) ...
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
                st.download_button("📥 ดาวน์โหลดไฟล์ที่ตรวจสอบแล้ว (PDF)", data=pdf_bytes.getvalue(), file_name="reviewed.pdf", mime="application/pdf")
                status_text.success("✅ เสร็จสิ้น")
                
            except Exception as e:
                st.error(f"เกิดข้อผิดพลาด: {e}")

        # --- กรณีเป็นไฟล์ PDF (ส่งทุกหน้าให้ AI วิเคราะห์พร้อมกัน) ---
        elif file_extension == "pdf":
            doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
            images_to_send = []
            
            # 1. แปลง PDF ทุกหน้าเป็นรูปภาพก่อน
            status_text.info(f"⏳ กำลังแปลง PDF ทั้ง {len(doc)} หน้า เป็นรูปภาพ...")
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=150)
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data)).convert("RGB")
                images_to_send.append(img)
            
            # 2. จัดเตรียม Prompt (ส่ง System Prompt + รูปภาพทั้งหมดเรียงกัน)
            prompt_content = [system_prompt] + images_to_send
            
            status_text.info("⏳ กำลังให้ AI ประมวลผลและเปรียบเทียบข้อมูลทุกหน้า (ใช้เวลาค่อนข้างนาน)...")
            try:
                # ส่งไปครั้งเดียว ได้ผลลัพธ์ครอบคลุมทุกหน้า
                response = model.generate_content(
                    prompt_content,
                    generation_config=genai.GenerationConfig(response_mime_type="application/json")
                )
                result_json = json.loads(response.text)
                
                st.subheader(f"📌 ภาพรวมโครงการ: {result_json.get('project_type', '')}")
                st.info(f"**สรุปข้อเสนอแนะรวม:** {result_json.get('summary', '')}")
                
                # 3. วนลูปวาดกรอบแดงลงในเอกสาร PDF และแสดงผลทีละหน้า
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    
                    # วาดกรอบแดงเฉพาะ issue ของหน้านี้
                    draw_on_pdf_page(page, result_json.get("issues", []), page_num)
                    
                    st.markdown(f"### 📄 หน้า {page_num + 1}")
                    
                    # พรีวิวหน้ากระดาษที่วาดแดงแล้ว
                    pix_annotated = page.get_pixmap(dpi=150)
                    img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                    st.image(img_annotated, use_column_width=True)
                    
                    # แสดงรายการที่ผิดพลาดของหน้านี้
                    issues_this_page = [i for i in result_json.get("issues", []) if i.get("page_index", -1) == page_num]
                    if issues_this_page:
                        with st.expander(f"📝 จุดที่ต้องแก้ไข (พบ {len(issues_this_page)} จุด)", expanded=True):
                            for idx, issue in enumerate(issues_this_page):
                                st.error(f"**- {issue.get('description')}**")
                    else:
                        st.success("✅ ไม่พบข้อผิดพลาดที่ชัดเจนในหน้านี้")
                    
                    st.markdown("---")

                # 4. ปุ่มดาวน์โหลด
                out_pdf_bytes = io.BytesIO()
                doc.save(out_pdf_bytes)
                status_text.success("✅ การวิเคราะห์และเปรียบเทียบเสร็จสมบูรณ์!")
                st.download_button(
                    label="📥 ดาวน์โหลดไฟล์ PDF สรุปผลตรวจสอบ (มีวงกรอบสีแดง)",
                    data=out_pdf_bytes.getvalue(),
                    file_name="reviewed_document_pro.pdf",
                    mime="application/pdf"
                )
                
            except Exception as e:
                st.error(f"เกิดข้อผิดพลาดระหว่างการเรียก AI (ไฟล์อาจใหญ่เกินไป หรือ Token หมด): {e}")
