import io
import json
import gc
import time
import fitz  # PyMuPDF
from PIL import Image
import streamlit as st
import google.generativeai as genai

st.set_page_config(page_title="ระบบผู้ช่วยตรวจแบบวิศวกรรม (AI Pro)", layout="wide")
st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทาน วิศวกรรมโยธา (Ai)")

# บันทึกผลการตรวจใน Session State ป้องกันการเสียโควต้าตรวจซ้ำ
if "review_results" not in st.session_state:
    st.session_state.review_results = {}

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

# โมเดลเวอร์ชันฟรีที่เสถียรที่สุดและมีโควต้า 1,500 ครั้ง/วัน
FREE_MODEL_NAME = 'gemini-1.5-flash'

system_prompt = """
คุณคือวิศวกรชลประทาน วิศวกรโยธา และผู้ตรวจทานแบบแปลนมืออาชีพ
ตรวจสอบแบบแปลนวิศวกรรมที่แนบมานี้อย่างละเอียดที่สุดในทุกองค์ประกอบ:

1. ดึงข้อมูลตารางชื่อแบบ (Title Block) บริเวณมุมขวาล่างของแบบแปลน:
   - หมายเลขแบบ (เช่น สชป.14-1-004781)
   - หมายเลขแผ่น (เช่น แผ่นที่ 1/12)
   - ชื่อแบบ/ชื่อโครงการ

2. ตรวจสอบความถูกต้อง 3 ด้านอย่างละเอียด:
   - วิศวกรรม (Engineering): ตรวจสอบระดับ Elevation, แรงดันน้ำ (Uplift), Factor of Safety, ความหนาคอนกรีต, ขนาดและระยะระยะเหล็กเสริม, สเกลอาคาร, มิติความกว้าง/ยาว/สูง
   - คำผิด (Typo): คำผิดภาษาไทย (สระ/วรรณยุกต์ตกหล่น) และศัพท์เทคนิคภาษาอังกฤษ
   - ข้อสังเกต (Observation): จุดที่ควรระมัดระวังในการก่อสร้าง ข้อกำหนดวัสดุ หรือข้อแนะนำเชิงเทคนิค

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

def process_page_with_retry(prompt_content, max_retries=3):
    """ส่งวิเคราะห์ทีละหน้า พร้อมระบบ Retry 3 รอบเมื่อเจอข้อผิดพลาดจาก Server"""
    model = genai.GenerativeModel(FREE_MODEL_NAME)
    
    for attempt in range(1, max_retries + 1):
        try:
            response = model.generate_content(
                prompt_content,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                    top_k=1,
                    top_p=0.1,
                ),
            )
            return response.text
        except Exception as e:
            err_str = str(e)
            # หากติด Rate limit 429 ให้รอนานขึ้น
            if "429" in err_str or "Quota" in err_str:
                time.sleep(8 * attempt)
            # หากติด Error 500 หรือ Timeout ให้รอ 3 วินาทีแล้วส่งซ้ำ
            elif "500" in err_str or "Internal" in err_str:
                time.sleep(3 * attempt)
            else:
                if attempt == max_retries:
                    raise e
                time.sleep(2)
    raise Exception("ไม่สามารถประมวลผลได้หลังจากพยายามซ้ำหลายครั้ง")

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

uploaded_file = st.file_uploader("อัปโหลดไฟล์แบบแปลน (PDF)", type=["pdf"])

if uploaded_file is not None and api_ready:
    doc = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
    total_pages = len(doc)
    
    st.sidebar.header("⚙️ การตั้งค่าการตรวจ")
    # ปรับค่าเริ่มต้นเป็น "ตรวจทุกหน้า"
    mode = st.sidebar.radio("รูปแบบการตรวจ:", ["ตรวจทุกหน้า (แนะนำ)", "เลือกเฉพาะบางหน้า"])
    
    pages_to_process = []
    if mode == "เลือกเฉพาะบางหน้า":
        selected_pages = st.sidebar.multiselect(
            "เลือกหน้าที่ต้องการตรวจ:", 
            options=list(range(1, total_pages + 1)),
            default=list(range(1, min(total_pages + 1, 4)))
        )
        pages_to_process = [p - 1 for p in selected_pages]
    else:
        pages_to_process = list(range(total_pages))

    col_btn1, col_btn2 = st.columns([2, 1])
    with col_btn1:
        run_btn = st.button("🚀 เริ่มการวิเคราะห์แบบแปลนทุกแผ่น")
    with col_btn2:
        if st.button("🔄 ล้างผลการตรวจเพื่อเริ่มใหม่"):
            st.session_state.review_results = {}
            st.rerun()

    if run_btn:
        progress_bar = st.progress(0)
        status_text = st.empty()
        current_dpi = 150  # ปรับ DPI เป็น 150 ให้ได้ภาพที่คมชัดพอสำหรับแปลน A1 แต่ประมวลผลเร็ว
        
        processed_count = 0
        total_selected = len(pages_to_process)
        
        for idx, page_num in enumerate(pages_to_process):
            page_key = f"{uploaded_file.name}_page_{page_num}"
            
            # หากเคยตรวจหน้านี้ไปแล้ว ให้ข้ามไปหน้าถัดไปทันที (ประหยัดโควต้า)
            if page_key in st.session_state.review_results:
                processed_count += 1
                progress_bar.progress(processed_count / total_selected)
                continue
                
            status_text.info(f"⏳ กำลังวิเคราะห์แผ่นที่ {page_num + 1} จากทั้งหมด {total_pages} แผ่น (กรุณารอระบบประมวลผลทีละแผ่น)...")
            
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=current_dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            
            try:
                resp_text = process_page_with_retry([system_prompt, img])
                result = clean_json_response(resp_text)
                
                if result:
                    st.session_state.review_results[page_key] = result
                else:
                    st.warning(f"⚠️ แผ่นที่ {page_num + 1} ไม่สามารถแปลงผลลัพธ์เป็น JSON ได้ ระบบจะบันทึกข้อความดิบไว้")
                    st.session_state.review_results[page_key] = {
                        "drawing_number": "ไม่ระบุ",
                        "sheet_number": f"แผ่นที่ {page_num + 1}",
                        "project_type": "แบบแปลนวิศวกรรม",
                        "issues": [{"type": "Observation", "error_keyword": "", "description": resp_text, "box_2d": [0,0,0,0]}]
                    }
                
                del pix
                del img
                gc.collect()
                
                # หน่วงเวลา 4 วินาทีเพื่อให้อยู่ในเกณฑ์ 15 RPM ของ Free Tier
                time.sleep(4)
                
            except Exception as e:
                st.error(f"❌ แผ่นที่ {page_num + 1} เกิดข้อผิดพลาด: {e} (ระบบกำลังข้ามไปตรวจแผ่นถัดไป)")
                # ใช้ continue เพื่อไม่ให้ขัดจังหวะการตรวจแผ่นที่เหลือ
                continue
            
            processed_count += 1
            progress_bar.progress(processed_count / total_selected)
        
        status_text.success(f"✅ ตรวจสอบเสร็จสิ้นทั้งหมด {len(st.session_state.review_results)} แผ่น!")

    # การแสดงผลลัพธ์ย้อนหลังทั้งหมด
    if st.session_state.review_results:
        st.markdown("---")
        st.markdown("## 📊 ผลการตรวจสอบแบบแปลนทั้งหมด")
        out_pdf = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
        
        # แสดงผลตามลำดับแผ่นที่เลือก
        for page_num in range(total_pages):
            page_key = f"{uploaded_file.name}_page_{page_num}"
            result_json = st.session_state.review_results.get(page_key)
            
            if result_json:
                dwg_no = result_json.get('drawing_number', 'ไม่ระบุ')
                sheet_no = result_json.get('sheet_number', f'แผ่นที่ {page_num + 1}')
                title = result_json.get('project_type', 'แบบแปลนวิศวกรรม')
                
                st.markdown(f"### 📄 [{dwg_no}] {sheet_no} : {title}")
                
                page = out_pdf.load_page(page_num)
                draw_spatial_guided_annotation(page, result_json.get("issues", []))
                
                pix_annotated = page.get_pixmap(dpi=120)
                img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                st.image(img_annotated, use_container_width=True)
                
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
        
        out_pdf_bytes = io.BytesIO()
        out_pdf.save(out_pdf_bytes)
        
        st.download_button(
            label="📥 ดาวน์โหลดไฟล์ PDF สรุปการตรวจครบทุกแผ่น (พร้อมวงกลมแดงและเลขแบบ)",
            data=out_pdf_bytes.getvalue(),
            file_name="Reviewed_Engineering_Drawing_All_Sheets.pdf",
            mime="application/pdf"
        )
