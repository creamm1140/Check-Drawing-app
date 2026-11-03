import io
import json
import gc
import time
import fitz  # PyMuPDF
from PIL import Image
import streamlit as st
import google.generativeai as genai

st.set_page_config(page_title="Hydraulic & Civil Review Pro", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา (Ai Pro)")

# รายชื่อโมเดลเรียงตามลำดับที่จะใช้สลับเมื่อติด Quota
CANDIDATE_MODELS = [
    'gemini-3.8-flash',
    'gemini-2.0-flash',
    'gemini-1.5-flash',
    'gemini-1.5-pro-latest'
]

@st.cache_resource
def init_genai():
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=api_key)
        return True
    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาดในการตั้งค่า API Key: {e}")
        return False

api_ready = init_genai()

system_prompt = """
คุณคือวิศวกรชลประทาน วิศวกรโยธา และผู้ตรวจทานแบบแปลนมืออาชีพ
ตรวจสอบแบบแปลนวิศวกรรมที่แนบมานี้อย่างละเอียดที่สุด:

1. ดึงข้อมูลตารางชื่อแบบ (Title Block) บริเวณมุมขวาล่างของแบบแปลน:
   - หมายเลขแบบ (เช่น สชป.14-1-004781)
   - หมายเลขแผ่น (เช่น แผ่นที่ 1/12)
   - ชื่อแบบ/ชื่อโครงการ

2. ตรวจสอบความถูกต้อง 3 ด้าน:
   - วิศวกรรม (Engineering): แรงดันน้ำ (Uplift), Factor of Safety, ความหนาคอนกรีต, ระดับ Elevation, มิติขนาดอาคาร (ความกว้าง/ยาว/สูง)
   - คำผิด (Typo): คำผิดภาษาไทย (สระ/วรรณยุกต์) และศัพท์เทคนิคภาษาอังกฤษ
   - ข้อสังเกต (Observation): จุดที่ควรระมัดระวังในการก่อสร้าง หรือข้อแนะนำเชิงเทคนิค

คุณต้องตอบกลับเป็น JSON เท่านั้น โครงสร้างดังนี้:
{
  "drawing_number": "หมายเลขแบบที่อ่านได้จาก Title Block (หากไม่มีให้ใส่ 'ไม่ระบุ')",
  "sheet_number": "หมายเลขแผ่น (เช่น แผ่นที่ 1/12)",
  "project_type": "ชื่อแบบ/ชื่อหัวเรื่องของหน้านี้",
  "issues": [
    {
      "type": "Typo หรือ Engineering หรือ Observation",
      "error_keyword": "เฉพาะคำผิดหรือตัวเลขเป๊ะๆ 1-3 คำที่ปรากฏบนแบบตรงจุดนั้น",
      "description": "อธิบายจุดที่ผิด ชี้แจงเหตุผลทางวิศวกรรม และระบุคำ/ค่าที่ถูกต้อง",
      "box_2d": [ymin, xmin, ymax, xmax]
    }
  ],
  "summary": "สรุปผลการตรวจสอบหน้านี้"
}

กฎพิกัด box_2d:
- สเกล 0 ถึง 1000 (ymin: บน, xmin: ซ้าย, ymax: ล่าง, xmax: ขวา)
- ต้องระบุพิกัด box_2d ให้โอบล้อมบริเวณข้อความหรือโครงสร้างที่ผิดนั้นๆ อย่างแท้จริง
"""

def clean_json_response(text):
    try:
        cleaned = text.replace('```json', '').replace('```', '').strip()
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None

def generate_with_fallback(prompt_content):
    """ฟังก์ชันเรียกใช้ AI โดยจะสลับโมเดลใหม่อัตโนมัติหากโมเดลแรกติด Quota Exceeded (429)"""
    for model_name in CANDIDATE_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                prompt_content,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                    top_k=1,
                    top_p=0.1,
                ),
            )
            return response.text, model_name
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "Quota" in err_msg:
                continue # หากโมเดลนี้โควต้าเต็ม ให้ข้ามไปลองโมเดลถัดไป
            else:
                raise e
    raise Exception("โควต้า API เต็มทุกโมเดลชั่วคราว กรุณาผูก Billing ใน Google AI Studio หรือลองใหม่ในภายหลัง")

def draw_spatial_guided_annotation(page, issues):
    rect_width = page.rect.width
    rect_height = page.rect.height
    
    for issue in issues:
        keyword = issue.get("error_keyword", "").strip()
        desc = issue.get("description", "ไม่ระบุรายละเอียด")
        i_type = issue.get("type", "Observation")
        box = issue.get("box_2d", [0, 0, 0, 0])
        
        annot_text = f"[{i_type.upper()}]\n{desc}"
        found_match = False
        
        if box and len(box) == 4 and box != [0, 0, 0, 0]:
            ymin, xmin, ymax, xmax = box
            ai_x0 = max(0, (xmin / 1000.0) * rect_width - 25)
            ai_y0 = max(0, (ymin / 1000.0) * rect_height - 25)
            ai_x1 = min(rect_width, (xmax / 1000.0) * rect_width + 25)
            ai_y1 = min(rect_height, (ymax / 1000.0) * rect_height + 25)
            ai_region = fitz.Rect(ai_x0, ai_y0, ai_x1, ai_y1)
            
            if keyword:
                text_instances = page.search_for(keyword, clip=ai_region, quads=True)
                if text_instances:
                    found_match = True
                    for quad in text_instances:
                        r = quad.rect
                        r_expanded = fitz.Rect(r.x0 - 4, r.y0 - 4, r.x1 + 4, r.y1 + 4)
                        page.draw_oval(r_expanded, color=(1, 0, 0), width=2)
                        
                        annot = page.add_text_annot(r_expanded.top_right, annot_text)
                        annot.set_colors(stroke=(1, 0, 0) if i_type.lower() == "typo" else (1, 0.5, 0))
                        annot.set_info(content=annot_text, title=f"ข้อผิดพลาด ({i_type})")
                        annot.update()
            
            if not found_match:
                page.draw_oval(ai_region, color=(1, 0, 0), width=2)
                annot = page.add_text_annot(fitz.Point(ai_x1, ai_y0), annot_text)
                annot.set_colors(stroke=(1, 0, 0) if i_type.lower() == "typo" else (1, 0.5, 0))
                annot.set_info(content=annot_text, title=f"ข้อผิดพลาด ({i_type})")
                annot.update()
                found_match = True
        
        if not found_match:
            annot = page.add_text_annot(fitz.Point(rect_width - 60, 60), annot_text)
            annot.set_colors(stroke=(0, 0, 1))
            annot.set_info(content=annot_text, title="ข้อสังเกตภาพรวม")
            annot.update()

uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลน (PDF)", type=["pdf"])

if uploaded_file is not None and api_ready:
    if st.button("🚀 เริ่มการวิเคราะห์แบบแปลน"):
        status_text = st.empty()
        
        try:
            doc = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
            current_dpi = 180
            
            for page_num in range(len(doc)):
                status_text.info(f"⏳ กำลังประมวลผลหน้า {page_num + 1} จาก {len(doc)}...")
                
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=current_dpi)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                
                prompt_content = [system_prompt, img]
                
                # เรียกประมวลผลผ่านฟังก์ชัน Fallback
                resp_text, used_model = generate_with_fallback(prompt_content)
                result_json = clean_json_response(resp_text)
                
                if result_json:
                    dwg_no = result_json.get('drawing_number', 'ไม่ระบุ')
                    sheet_no = result_json.get('sheet_number', f'แผ่นที่ {page_num + 1}')
                    title = result_json.get('project_type', 'แบบแปลนวิศวกรรม')
                    
                    st.markdown(f"### 📄 [{dwg_no}] {sheet_no} : {title} *(ใช้โมเดล: {used_model})*")
                    
                    draw_spatial_guided_annotation(page, result_json.get("issues", []))
                    
                    pix_annotated = page.get_pixmap(dpi=120)
                    img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                    st.image(img_annotated, use_column_width=True)
                    
                    issues_this_page = result_json.get("issues", [])
                    if issues_this_page:
                        with st.expander(f"📝 รายการข้อผิดพลาดที่พบ ({dwg_no} - {sheet_no})", expanded=True):
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
                
                del pix
                del img
                gc.collect()
                time.sleep(1) # เว้นจังหวะ 1 วินาทีลดภาระ Rate limit

            out_pdf_bytes = io.BytesIO()
            doc.save(out_pdf_bytes)
            status_text.success("✅ ตรวจสอบเสร็จสมบูรณ์!")
            
            st.download_button(
                label="📥 ดาวน์โหลดไฟล์ PDF (พร้อมวงกลมระบุจุดผิดและเลขแบบ)",
                data=out_pdf_bytes.getvalue(),
                file_name="Reviewed_Engineering_Drawing_Indexed.pdf",
                mime="application/pdf"
            )
                
        except Exception as e:
            status_text.error(f"❌ เกิดข้อผิดพลาด: {e}")
