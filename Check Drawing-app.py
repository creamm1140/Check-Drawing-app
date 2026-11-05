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

@st.cache_data(ttl=1800)
def get_valid_vision_models():
    """สแกนหารายชื่อโมเดล Vision ที่ใช้งานได้จริงในบัญชีแบบ Dynamic เพื่อป้องกัน Error 404"""
    invalid_keywords = ['tts', 'audio', 'embed', 'imagen', 'realtime']
    valid_models = []
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                name = m.name.replace('models/', '')
                if not any(k in name.lower() for k in invalid_keywords):
                    valid_models.append(name)
        
        priority_candidates = [
            'gemini-1.5-flash-latest',
            'gemini-1.5-flash',
            'gemini-1.5-flash-8b',
            'gemini-2.0-flash',
            'gemini-2.5-flash',
            'gemini-3.8-flash',
            'gemini-1.5-pro-latest',
            'gemini-1.5-pro'
        ]
        
        ordered = [m for m in priority_candidates if m in valid_models]
        for m in valid_models:
            if m not in ordered:
                ordered.append(m)
                
        return ordered if ordered else ['gemini-1.5-flash-latest', 'gemini-1.5-flash']
    except Exception:
        return ['gemini-1.5-flash-latest', 'gemini-1.5-flash']

system_prompt = """
คุณคือวิศวกรชลประทาน วิศวกรโยธา และผู้ตรวจทานแบบแปลนมืออาชีพ
ตรวจสอบแบบแปลนวิศวกรรมที่แนบมานี้อย่างละเอียดที่สุดในทุกองค์ประกอบ:

1. ดึงข้อมูลตารางชื่อแบบ (Title Block) บริเวณมุมขวาล่างของแบบแปลน:
   - หมายเลขแบบ (เช่น สชป.14-1-004781)
   - หมายเลขแผ่น (เช่น แผ่นที่ 1/12)
   - ชื่อแบบ/ชื่อโครงการ

2. ตรวจสอบความถูกต้อง 3 ด้านอย่างละเอียด (ระบุคำอธิบายทั้งหมดเป็นภาษาไทยทางการวิศวกรรม):
   - คำผิด (Typo): คำผิดภาษาไทย (สระ/วรรณยุกต์ตกหล่น), ฟอนต์ SHX CAD เพี้ยน (เช่น 20000.20, DEOTEXTILE, สาย ม่วง), และศัพท์เทคนิคภาษาอังกฤษ
   - วิศวกรรม (Engineering): ตรวจสอบระดับ Elevation, แรงดันน้ำ (Uplift), Factor of Safety, ความหนาคอนกรีต, ขนาดและระยะเหล็กเสริม, สเกลอาคาร, มิติความกว้าง/ยาว/สูง, การเจาะสำรวจชั้นดิน Boring Log
   - ข้อสังเกต (Observation): จุดที่ควรระมัดระวังในการก่อสร้าง ข้อกำหนดวัสดุ หรือข้อแนะนำเชิงเทคนิคสนาม

คุณต้องตอบกลับเป็น JSON ภาษาไทยเท่านั้น โครงสร้างดังนี้:
{
  "drawing_number": "หมายเลขแบบที่อ่านได้จาก Title Block (เช่น สชป.14-1-004781)",
  "sheet_number": "หมายเลขแผ่น (เช่น แผ่นที่ 1/12)",
  "project_type": "ชื่อแบบ/ชื่อหัวเรื่องของหน้านี้",
  "issues": [
    {
      "type": "Typo หรือ Engineering หรือ Observation",
      "error_keyword": "เฉพาะคำผิด ตัวเลข หรือข้อความเป๊ะๆ 1-3 คำที่ปรากฏบนแบบแปลน",
      "description": "คำอธิบายภาษาไทยชี้แจงเหตุผลทางวิศวกรรม ระบุจุดที่ผิดอย่างชัดเจน และแนะนำสิ่งที่ถูกต้อง",
      "box_2d": [ymin, xmin, ymax, xmax]
    }
  ],
  "summary": "สรุปผลการตรวจสอบหน้านี้เป็นภาษาไทย"
}

กฎพิกัด box_2d:
- สเกล 0 ถึง 1000 (ymin: บน, xmin: ซ้าย, ymax: ล่าง, xmax: ขวา)
- ระบุพิกัด box_2d ให้โอบล้อมบริเวณข้อความหรือโครงสร้างที่ผิดนั้นๆ
"""

def clean_json_response(text):
    """แปลงข้อความจาก AI ให้เป็น Python dictionary อย่างปลอดภัย ป้องกันปัญหา List vs Dict"""
    try:
        cleaned = text.replace('```json', '').replace('```', '').strip()
        data = json.loads(cleaned)
        
        # หาก AI คืนค่ากลับมาเป็น List ให้ดึง Element แรกที่เป็น Dict ออกมาใช้งาน
        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict):
                data = data[0]
            else:
                return None
                
        if isinstance(data, dict):
            # ตรวจสอบโครงสร้างของ issues ให้เป็น List เสมอ
            issues = data.get("issues", [])
            if isinstance(issues, dict):
                data["issues"] = [issues]
            elif not isinstance(issues, list):
                data["issues"] = []
            return data
            
        return None
    except Exception:
        return None

def process_page_with_dynamic_fallback(prompt_content):
    """ส่งคำขอไปยังโมเดลที่เปิดใช้งานจริง หากเจอ 404 หรือ 429 จะสลับโมเดลให้อัตโนมัติ"""
    models_to_try = get_valid_vision_models()
    last_error = None
    
    for model_name in models_to_try:
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
            err_str = str(e)
            last_error = f"{model_name}: {err_str}"
            if any(k in err_str for k in ["404", "429", "400", "Quota", "not found"]):
                continue
            elif "500" in err_str or "Internal" in err_str:
                time.sleep(3)
                try:
                    response = model.generate_content(prompt_content)
                    return response.text, model_name
                except Exception:
                    continue
            else:
                continue
                
    raise Exception(f"ไม่สามารถประมวลผลได้ในขณะนี้: {last_error}")

def draw_spatial_guided_annotation(page, issues):
    """วาดวงกลมสีแดงและโน้ตข้อความภาษาไทย โดยใช้ Text Anchor Matching บน Vector PDF"""
    rect_width = page.rect.width
    rect_height = page.rect.height
    
    if not isinstance(issues, list):
        return
    
    for issue in issues:
        if not isinstance(issue, dict):
            continue
            
        keyword = str(issue.get("error_keyword", "")).strip()
        desc = str(issue.get("description", "ไม่ระบุรายละเอียด"))
        i_type = str(issue.get("type", "Observation"))
        box = issue.get("box_2d", [0, 0, 0, 0])
        
        stroke_color = (1, 0, 0) if i_type.lower() == "typo" else ((1, 0.4, 0) if i_type.lower() == "engineering" else (0, 0.4, 1))
        annot_text = f"[{i_type.upper()}]\nคำ/จุดสังเกต: {keyword}\nรายละเอียด: {desc}"
        found_match = False
        
        # 1. ค้นหาคำเป๊ะๆ บน Text Layer ของ PDF (Text Anchor Matching)
        if keyword and len(keyword) >= 2:
            try:
                text_instances = page.search_for(keyword)
                if text_instances:
                    found_match = True
                    for r in text_instances:
                        r_expanded = fitz.Rect(r.x0 - 5, r.y0 - 5, r.x1 + 5, r.y1 + 5)
                        page.draw_oval(r_expanded, color=stroke_color, width=2)
                        
                        annot = page.add_text_annot(r_expanded.top_right, annot_text)
                        annot.set_colors(stroke=stroke_color)
                        annot.set_info(content=annot_text, title=f"ข้อผิดพลาด ({i_type})")
                        annot.update()
            except Exception:
                found_match = False
        
        # 2. ปรับการแกะค่า box_2d ป้องกันกรณี AI ส่ง format มาผิดประเภท
        ymin, xmin, ymax, xmax = 0, 0, 0, 0
        if isinstance(box, list) and len(box) == 4:
            try:
                ymin, xmin, ymax, xmax = [float(v) for v in box]
            except (ValueError, TypeError):
                ymin, xmin, ymax, xmax = 0, 0, 0, 0
        elif isinstance(box, dict):
            try:
                ymin = float(box.get("ymin", box.get("top", 0)))
                xmin = float(box.get("xmin", box.get("left", 0)))
                ymax = float(box.get("ymax", box.get("bottom", 0)))
                xmax = float(box.get("xmax", box.get("right", 0)))
            except (ValueError, TypeError):
                ymin, xmin, ymax, xmax = 0, 0, 0, 0

        if not found_match and (ymin > 0 or xmin > 0 or ymax > 0 or xmax > 0):
            ai_x0 = max(0, (xmin / 1000.0) * rect_width - 15)
            ai_y0 = max(0, (ymin / 1000.0) * rect_height - 15)
            ai_x1 = min(rect_width, (xmax / 1000.0) * rect_width + 15)
            ai_y1 = min(rect_height, (ymax / 1000.0) * rect_height + 15)
            
            if ai_x1 > ai_x0 and ai_y1 > ai_y0:
                ai_region = fitz.Rect(ai_x0, ai_y0, ai_x1, ai_y1)
                page.draw_oval(ai_region, color=stroke_color, width=2)
                
                annot = page.add_text_annot(fitz.Point(ai_x1, ai_y0), annot_text)
                annot.set_colors(stroke=stroke_color)
                annot.set_info(content=annot_text, title=f"ข้อผิดพลาด ({i_type})")
                annot.update()
                found_match = True
        
        # 3. กรณีเป็นข้อสังเกตภาพรวมแผ่น
        if not found_match:
            annot = page.add_text_annot(fitz.Point(rect_width - 60, 60), annot_text)
            annot.set_colors(stroke=(0, 0.4, 1))
            annot.set_info(content=annot_text, title="ข้อสังเกตภาพรวม")
            annot.update()

uploaded_file = st.file_uploader("อัปโหลดไฟล์แบบแปลน (PDF)", type=["pdf"])

if uploaded_file is not None and api_ready:
    doc = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
    total_pages = len(doc)
    
    st.sidebar.header("⚙️ การตั้งค่าการตรวจ")
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
        current_dpi = 150
        
        processed_count = 0
        total_selected = len(pages_to_process)
        
        for idx, page_num in enumerate(pages_to_process):
            page_key = f"{uploaded_file.name}_page_{page_num}"
            
            if page_key in st.session_state.review_results:
                processed_count += 1
                progress_bar.progress(processed_count / total_selected)
                continue
                
            status_text.info(f"⏳ กำลังวิเคราะห์แผ่นที่ {page_num + 1} จากทั้งหมด {total_pages} แผ่น (หน่วงเวลา 10 วินาทีเพื่อความเสถียร)...")
            
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=current_dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            
            try:
                resp_text, used_model = process_page_with_dynamic_fallback([system_prompt, img])
                result = clean_json_response(resp_text)
                
                if isinstance(result, dict):
                    result["used_model"] = used_model
                    st.session_state.review_results[page_key] = result
                else:
                    st.session_state.review_results[page_key] = {
                        "drawing_number": "ไม่ระบุ",
                        "sheet_number": f"แผ่นที่ {page_num + 1}",
                        "project_type": "แบบแปลนวิศวกรรม",
                        "used_model": used_model,
                        "issues": [{"type": "Observation", "error_keyword": "", "description": resp_text, "box_2d": [0,0,0,0]}]
                    }
                
                del pix
                del img
                gc.collect()
                
                time.sleep(10)
                
            except Exception as e:
                st.error(f"❌ แผ่นที่ {page_num + 1} เกิดข้อผิดพลาด: {e}")
                continue
            
            processed_count += 1
            progress_bar.progress(processed_count / total_selected)
        
        status_text.success(f"✅ ตรวจสอบเสร็จสิ้นทั้งหมด {len(st.session_state.review_results)} แผ่น!")

    if st.session_state.review_results:
        st.markdown("---")
        st.markdown("## 📊 ผลการตรวจสอบแบบแปลนทั้งหมด")
        out_pdf = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
        
        for page_num in range(total_pages):
            page_key = f"{uploaded_file.name}_page_{page_num}"
            result_json = st.session_state.review_results.get(page_key)
            
            if isinstance(result_json, dict):
                dwg_no = result_json.get('drawing_number', 'ไม่ระบุ')
                sheet_no = result_json.get('sheet_number', f'แผ่นที่ {page_num + 1}')
                title = result_json.get('project_type', 'แบบแปลนวิศวกรรม')
                model_used = result_json.get('used_model', 'ไม่ระบุ')
                
                st.markdown(f"### 📄 [{dwg_no}] {sheet_no} : {title} *(ประมวลผลด้วยโมเดล: {model_used})*")
                
                page = out_pdf.load_page(page_num)
                draw_spatial_guided_annotation(page, result_json.get("issues", []))
                
                pix_annotated = page.get_pixmap(dpi=120)
                img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                st.image(img_annotated, use_container_width=True)
                
                issues_this_page = result_json.get("issues", [])
                if isinstance(issues_this_page, list) and issues_this_page:
                    with st.expander(f"📝 รายการข้อผิดพลาดและข้อสังเกตภาษาไทย ({dwg_no} - {sheet_no})", expanded=True):
                        for issue in issues_this_page:
                            if not isinstance(issue, dict):
                                continue
                            i_type = str(issue.get("type", "Observation"))
                            kw = str(issue.get("error_keyword", ""))
                            desc = str(issue.get("description", ""))
                            
                            if i_type.lower() == "typo":
                                st.error(f"🔤 **[คำผิด / ฟอนต์เพี้ยน: '{kw}']:** {desc}")
                            elif i_type.lower() == "engineering":
                                st.warning(f"📐 **[ข้อพิจารณาทางวิศวกรรม: '{kw}']:** {desc}")
                            else:
                                st.info(f"💡 **[ข้อสังเกตสนาม / การก่อสร้าง]:** {desc}")
                st.markdown("---")
        
        out_pdf_bytes = io.BytesIO()
        out_pdf.save(out_pdf_bytes)
        
        st.download_button(
            label="📥 ดาวน์โหลดไฟล์ PDF สรุปการตรวจครบทุกแผ่น (พร้อมวงกลมแดงและโน้ตภาษาไทย)",
            data=out_pdf_bytes.getvalue(),
            file_name="Reviewed_Engineering_Drawing_All_Sheets.pdf",
            mime="application/pdf"
        )
