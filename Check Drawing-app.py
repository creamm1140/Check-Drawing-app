import streamlit as st
import fitz  # PyMuPDF
from PIL import Image
import io
import json
import gc
import google.generativeai as genai

st.set_page_config(page_title="Hydraulic & Civil Review Pro", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา (Pro)")

# 1. ตั้งค่า API Key และ Model
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    # ใช้รุ่นเดิมที่คุณรันผ่านและเสถียรที่สุด
    model = genai.GenerativeModel('gemini-3.5-flash-lite') 
except Exception as e:
    model = None
    st.error(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ: {e}")

# 2. System Prompt อัปเกรดเพื่อเน้นหาคำผิด (Typo) และแยกประเภทปัญหา
system_prompt = """
คุณคือวิศวกรชลประทานและวิศวกรโยธาระดับเชี่ยวชาญ และเป็น "ผู้พิสูจน์อักษร" ที่มีความละเอียดรอบคอบขั้นสูงสุด
ฉันจะส่งรูปภาพแบบแปลนหรือรายการคำนวณให้คุณ (อาจมีหลายหน้า)
หน้าที่ของคุณคือตรวจสอบอย่างละเอียดตามหลักเกณฑ์นี้:

1. วิศวกรรม (Engineering): ตรวจสอบความถูกต้องของหลักการ เช่น แรงดันน้ำ (Uplift), Factor of Safety, ความหนาคอนกรีต, การเลือกใช้ท่อ
2. คำผิดคำถูก (Typo & Wording): **สำคัญมาก** สแกนหาคำผิดทุกตัวอักษร! ค้นหาการสะกดผิด, พิมพ์ตกหล่น, ใช้คำผิดบริบท, วรรณยุกต์ผิด ทั้งภาษาไทยและศัพท์เทคนิคภาษาอังกฤษ (เช่น Uplift พิมพ์ตก t, Elevation, Section) หากพบให้ระบุคำที่ผิดและคำที่ถูกต้อง
3. ความสอดคล้อง (Cross-Reference): เปรียบเทียบตัวเลขข้ามหน้า ว่าขัดแย้งกันหรือไม่
4. ข้อบังคับ (Mandatory Observation): ต้องหาข้อสังเกต หรือจุดที่ควรระวังอย่างน้อย 1-2 จุดต่อหน้าเสมอ ห้ามส่งกลับมาว่าไม่พบปัญหา

คุณต้องตอบกลับมาในรูปแบบ JSON เท่านั้น โครงสร้างดังนี้:
{
  "project_type": "ประเภทของโครงการ/งาน",
  "issues": [
    {
      "page_index": [ระบุ index ของหน้า เริ่มที่ 0],
      "type": "Engineering หรือ Typo หรือ Observation",
      "description": "ระบุจุดที่ผิดและบอกคำที่ถูกต้อง (ถ้าเป็นการพิมพ์ผิด) หรืออธิบายข้อสังเกต",
      "box_2d": [ymin, xmin, ymax, xmax] 
    }
  ],
  "summary": "สรุปผลการตรวจสอบรวม"
}

กฎพิกัด box_2d:
- ใช้สเกล 0 ถึง 1000 สำหรับรูปภาพในหน้านั้นๆ (ซ้ายบน 0,0 ขวาล่าง 1000,1000)
- ymin: บน, xmin: ซ้าย, ymax: ล่าง, xmax: ขวา
- หากชี้พิกัดชัดเจนไม่ได้ ให้ส่ง [0, 0, 0, 0]
"""

def clean_json_response(text):
    try:
        cleaned = text.replace('```json', '').replace('```', '').strip()
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None

def draw_and_annotate_on_pdf(page, issues, current_page_index):
    """ฟังก์ชันวาดกรอบสีแดง และฝังข้อความคอมเมนต์ (Sticky Note) ลงในไฟล์ PDF"""
    rect_width = page.rect.width
    rect_height = page.rect.height
    
    for issue in issues:
        if issue.get("page_index", 0) == current_page_index:
            box = issue.get("box_2d", [0, 0, 0, 0])
            desc = issue.get("description", "ไม่ระบุรายละเอียด")
            i_type = issue.get("type", "Observation")
            
            # ข้อความที่จะถูกฝังเข้าไปใน PDF
            annot_text = f"[{i_type.upper()}]\n{desc}"
            
            if box and len(box) == 4 and box != [0, 0, 0, 0]:
                ymin, xmin, ymax, xmax = box
                x0 = (xmin / 1000.0) * rect_width
                y0 = (ymin / 1000.0) * rect_height
                x1 = (xmax / 1000.0) * rect_width
                y1 = (ymax / 1000.0) * rect_height
                
                rect = fitz.Rect(x0, y0, x1, y1)
                
                # 1. วาดกรอบสีแดง
                page.draw_rect(rect, color=(1, 0, 0), width=2)
                
                # 2. เพิ่ม Sticky Note ไว้ที่มุมขวาบนของกรอบสีแดง (เพื่อให้คลิกอ่านได้)
                annot = page.add_text_annot(fitz.Point(x1, y0), annot_text)
                annot.set_colors(stroke=(1, 0, 0) if i_type.lower() == "typo" else (1, 0, 0))
                annot.set_info(content=annot_text, title=f"จุดที่ต้องแก้ไข ({i_type})")
                annot.update()
            else:
                # ถ้าระบุพิกัดในภาพไม่ได้ ให้แปะโน้ตไว้ที่มุมซ้ายบนสุดของกระดาษ
                annot = page.add_text_annot(fitz.Point(30, 30), annot_text)
                annot.set_colors(stroke=(1, 0.5, 0)) # สีส้มสำหรับภาพรวม
                annot.set_info(content=annot_text, title="ข้อสังเกตภาพรวม")
                annot.update()

uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลน (PDF หรือ รูปภาพ)", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None and model is not None:
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    if st.button("🚀 เริ่มการวิเคราะห์หาคำผิดและข้อสังเกต"):
        status_text = st.empty()
        status_text.info("⏳ กำลังเตรียมไฟล์...")
        
        try:
            # รวมการทำงาน: ไม่ว่าจะเป็นรูปหรือ PDF จะแปลงให้เป็น PDF (fitz document) เพื่อให้ฝังคอมเมนต์ได้เหมือนกันหมด
            if file_extension in ["png", "jpg", "jpeg"]:
                img_data = uploaded_file.getvalue()
                pdf_bytes = fitz.open(stream=img_data, filetype=file_extension).convert_to_pdf()
                doc = fitz.open("pdf", pdf_bytes)
            else:
                doc = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
            
            images_to_send = []
            
            # ปรับ DPI ตามจำนวนหน้าเพื่อคุม RAM
            current_dpi = 150 if len(doc) <= 3 else 100
            status_text.info(f"⏳ กำลังแปลงเอกสาร (พบ {len(doc)} หน้า)...")
            
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=current_dpi)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                images_to_send.append(img)
                
                del pix
                gc.collect()

            status_text.info("⏳ AI กำลังสแกนหาคำผิดและตรวจสอบหลักการทางวิศวกรรม (อาจใช้เวลาสักครู่)...")
            prompt_content = [system_prompt] + images_to_send
            
            response = model.generate_content(
                prompt_content,
                generation_config=genai.GenerationConfig(response_mime_type="application/json")
            )
            
            result_json = clean_json_response(response.text)
            
            if result_json:
                st.subheader(f"📌 โครงการ: {result_json.get('project_type', 'ไม่ระบุ')}")
                st.info(f"**💡 สรุปผล:** {result_json.get('summary', '')}")
                
                # วนลูปวาดกรอบและฝังคอมเมนต์ลงใน PDF แต่ละหน้า
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    draw_and_annotate_on_pdf(page, result_json.get("issues", []), page_num)
                    
                    st.markdown(f"### 📄 หน้า {page_num + 1}")
                    
                    # เรนเดอร์หน้าที่มีการวาดกรอบแดงแล้วมาโชว์ในเว็บ
                    pix_annotated = page.get_pixmap(dpi=100)
                    img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                    st.image(img_annotated, use_column_width=True)
                    
                    issues_this_page = [i for i in result_json.get("issues", []) if i.get("page_index", -1) == page_num]
                    
                    # แยกประเภทจุดที่ผิดให้ดูง่ายๆ ในหน้าเว็บ
                    if issues_this_page:
                        with st.expander(f"📝 จุดที่ต้องตรวจสอบ (หน้า {page_num + 1})", expanded=True):
                            for issue in issues_this_page:
                                i_type = issue.get("type", "Observation")
                                desc = issue.get("description", "")
                                if i_type.lower() == "typo":
                                    st.error(f"🔤 **[คำผิด/ตกหล่น]:** {desc}")
                                elif i_type.lower() == "engineering":
                                    st.warning(f"📐 **[วิศวกรรม]:** {desc}")
                                else:
                                    st.info(f"💡 **[ข้อสังเกต]:** {desc}")
                    else:
                        st.success("✅ ไม่พบปัญหาในหน้านี้")
                    
                    st.markdown("---")

                # สร้างไฟล์ PDF ฉบับสมบูรณ์ให้ดาวน์โหลด
                out_pdf_bytes = io.BytesIO()
                doc.save(out_pdf_bytes)
                status_text.success("✅ การตรวจสอบเสร็จสมบูรณ์! สามารถดาวน์โหลดไฟล์ผลลัพธ์ได้ด้านล่าง")
                
                st.download_button(
                    label="📥 ดาวน์โหลดไฟล์ PDF (พร้อมกรอบแดง และ Note คอมเมนต์)",
                    data=out_pdf_bytes.getvalue(),
                    file_name="Reviewed_Engineering_Drawing.pdf",
                    mime="application/pdf",
                    help="เมื่อเปิด PDF นี้ในคอมพิวเตอร์ คุณสามารถเอาเมาส์ชี้หรือคลิกที่ไอคอน Note สีแดงข้างๆ กรอบ เพื่ออ่านข้อเสนอแนะได้"
                )
            else:
                status_text.error("❌ ไม่สามารถประมวลผลข้อความจาก AI ได้ กรุณาลองใหม่อีกครั้ง")
                
        except Exception as e:
            status_text.error(f"❌ เกิดข้อผิดพลาด: {e}")
