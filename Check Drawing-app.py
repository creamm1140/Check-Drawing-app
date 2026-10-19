import streamlit as st
import fitz  # PyMuPDF
from PIL import Image
import io
import json
import gc
import google.generativeai as genai

st.set_page_config(page_title="Hydraulic & Civil Review Pro", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา (Pro)")

try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    # แนะนำใช้รุ่นที่เสถียรที่สุดที่คุณมี
    model = genai.GenerativeModel('gemini-3.5-flash-lite') 
except Exception as e:
    model = None
    st.error(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ: {e}")

# 1. ปรับ Prompt ใหม่: เลิกขอพิกัด แต่ขอ "Keyword" ที่ผิดแทน
system_prompt = """
คุณคือวิศวกรชลประทาน วิศวกรโยธา และผู้พิสูจน์อักษรระดับเชี่ยวชาญ
ตรวจสอบรูปภาพแบบแปลนหรือรายการคำนวณที่แนบมานี้อย่างละเอียดที่สุด:

1. วิศวกรรม (Engineering): ตรวจสอบความถูกต้องของหลักการ เช่น แรงดันน้ำ (Uplift), Factor of Safety, ความหนาคอนกรีต, ระดับ Elevation
2. คำผิด (Typo): สแกนหาคำผิดทุกตัวอักษร ทั้งภาษาไทยและศัพท์เทคนิคภาษาอังกฤษ (เช่น พิมพ์ Uplift ตกหล่น, Elevation พิมพ์ผิด)
3. ข้อสังเกต (Observation): หากไม่พบจุดผิดเลย ให้เสนอแนะจุดที่ควรระวังในการก่อสร้าง

คุณต้องตอบกลับมาเป็น JSON เท่านั้น โครงสร้างดังนี้:
{
  "project_type": "ประเภทของโครงการ/งานที่ตรวจพบในหน้านี้",
  "issues": [
    {
      "type": "Typo หรือ Engineering หรือ Observation",
      "error_keyword": "คำ หรือ ตัวเลข ที่ผิดเป๊ะๆ (เช่น 'Uplif', 'El. +84.000') *สำคัญมาก: ต้องสะกดให้ตรงกับในภาพเพื่อให้ระบบค้นหาพิกัดเจอ หากเป็นการแนะนำทั่วไปให้ใส่ค่าว่าง '' ",
      "description": "อธิบายว่าผิดอย่างไร และควรแก้ไขเป็นอะไร"
    }
  ],
  "summary": "สรุปผลการตรวจสอบของหน้านี้"
}
"""

def clean_json_response(text):
    try:
        cleaned = text.replace('```json', '').replace('```', '').strip()
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None

def draw_and_annotate_on_pdf(page, issues):
    """ฟังก์ชันวาดกรอบโดยใช้ PyMuPDF ค้นหาข้อความ (แม่นยำ 100%)"""
    for issue in issues:
        keyword = issue.get("error_keyword", "").strip()
        desc = issue.get("description", "ไม่ระบุรายละเอียด")
        i_type = issue.get("type", "Observation")
        
        annot_text = f"[{i_type.upper()}]\n{desc}"
        found_coordinates = False
        
        # 1. ถ้าระบุ Keyword ให้ใช้ระบบค้นหาข้อความใน PDF
        if keyword:
            # ค้นหาตำแหน่งของคำนั้นในหน้า PDF ตรงๆ
            text_instances = page.search_for(keyword)
            if text_instances:
                found_coordinates = True
                for inst in text_instances:
                    # วาดกรอบสีแดงทับคำที่เจอแบบเป๊ะๆ
                    page.draw_rect(inst, color=(1, 0, 0), width=2)
                    
                    # แปะ Note ไว้ข้างๆ
                    annot = page.add_text_annot(inst.top_right, annot_text)
                    annot.set_colors(stroke=(1, 0, 0) if i_type.lower() == "typo" else (1, 0.5, 0))
                    annot.set_info(content=annot_text, title=f"ข้อผิดพลาด ({i_type})")
                    annot.update()
        
        # 2. ถ้าหา Keyword ไม่เจอ หรือเป็นข้อเสนอแนะภาพรวม ให้แปะ Note ไว้มุมขวาบน
        if not found_coordinates:
            rect_width = page.rect.width
            annot = page.add_text_annot(fitz.Point(rect_width - 50, 50), annot_text)
            annot.set_colors(stroke=(0, 0, 1)) # สีน้ำเงินสำหรับข้อสังเกตภาพรวม
            annot.set_info(content=annot_text, title="ข้อสังเกต / ภาพรวม")
            annot.update()

uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลน (PDF)", type=["pdf"])

if uploaded_file is not None and model is not None:
    if st.button("🚀 เริ่มการวิเคราะห์ทีละหน้า (แม่นยำสูง)"):
        status_text = st.empty()
        
        try:
            doc = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
            out_pdf = fitz.open() # สร้างไฟล์ PDF เปล่าสำหรับเก็บหน้าที่แก้แล้ว
            
            # ปรับ DPI ให้สูงขึ้นเพื่อให้ AI อ่านตัวเลขเล็กๆ ออก
            current_dpi = 150 
            
            for page_num in range(len(doc)):
                status_text.info(f"⏳ กำลังประมวลผลหน้า {page_num + 1} จาก {len(doc)}...")
                
                # โหลดหน้ากระดาษ
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=current_dpi)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                
                # *** ส่งประมวลผลทีละหน้า เพื่อลดการหลอนของ AI ***
                prompt_content = [system_prompt, img]
                
                response = model.generate_content(
                    prompt_content,
                    generation_config=genai.GenerationConfig(response_mime_type="application/json")
                )
                
                result_json = clean_json_response(response.text)
                
                if result_json:
                    st.markdown(f"### 📄 หน้า {page_num + 1}: {result_json.get('project_type', 'ไม่ระบุ')}")
                    
                    # นำผลลัพธ์ไปวาดกรอบ
                    draw_and_annotate_on_pdf(page, result_json.get("issues", []))
                    
                    # เรนเดอร์ภาพโชว์บนหน้าเว็บ
                    pix_annotated = page.get_pixmap(dpi=100)
                    img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                    st.image(img_annotated, use_column_width=True)
                    
                    # แสดงรายงาน
                    issues_this_page = result_json.get("issues", [])
                    if issues_this_page:
                        with st.expander(f"📝 ข้อเสนอแนะ (หน้า {page_num + 1})", expanded=True):
                            for issue in issues_this_page:
                                i_type = issue.get("type", "Observation")
                                kw = issue.get("error_keyword", "")
                                desc = issue.get("description", "")
                                
                                if i_type.lower() == "typo":
                                    st.error(f"🔤 **[คำผิด: '{kw}']:** {desc}")
                                elif i_type.lower() == "engineering":
                                    st.warning(f"📐 **[วิศวกรรม: '{kw}']:** {desc}")
                                else:
                                    st.info(f"💡 **[ข้อสังเกต]:** {desc}")
                    
                    st.markdown("---")
                
                # เคลียร์ RAM หลังจบแต่ละหน้า
                del pix
                del img
                gc.collect()

            # สร้างไฟล์ PDF รวมทุกหน้าสำหรับดาวน์โหลด
            out_pdf_bytes = io.BytesIO()
            doc.save(out_pdf_bytes)
            status_text.success("✅ ตรวจสอบเสร็จสมบูรณ์!")
            
            st.download_button(
                label="📥 ดาวน์โหลดไฟล์ PDF (พร้อมกรอบแดงที่แม่นยำขึ้น)",
                data=out_pdf_bytes.getvalue(),
                file_name="Reviewed_Engineering_Drawing_Fixed.pdf",
                mime="application/pdf"
            )
                
        except Exception as e:
            status_text.error(f"❌ เกิดข้อผิดพลาด: {e}")
