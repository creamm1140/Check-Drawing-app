import io
import json
import gc
import time
import re
from datetime import datetime
import fitz  # PyMuPDF
from PIL import Image
import streamlit as st
import google.generativeai as genai
import pandas as pd

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
   - คำผิด (Typo): คำผิดภาษาไทย (สระ/วรรณยุกต์ตกหล่น), ฟอนต์ SHX CAD เพี้ยน (เช่น 20000.20, DEOTEXTILE, สาย ม่วง),ชื่อที่เป็นชื่อที่ตำแหน่งที่ตั้งประเทศไทย (เช่น ชื่อถนน, ชื่อตำบล อำเภอ จังหวัด, เลขระวาง) และศัพท์เทคนิคภาษาอังกฤษ
   - วิศวกรรม (Engineering): ตรวจสอบระดับ Elevation, แรงดันน้ำ (Uplift), Factor of Safety, ความหนาคอนกรีต, ขนาดและระยะเหล็กเสริม, สเกลอาคาร, มิติความกว้าง/ยาว/สูง, การเจาะสำรวจชั้นดิน Boring Log
   - ข้อสังเกต (Observation): จุดที่ควรระมัดระวังในการก่อสร้าง ข้อกำหนดวัสดุ หรือข้อแนะนำเชิงเทคนิคสนาม
   - ตรวจสอบความสัมพันธ์ที่เชื่อมกัน เช่น การอ้างอิงเลขแบบถึงกันแต่ละหน้า ,ระดับ Elevation แต่ละจุดของอาคาร ควรตรงกันทุกแผ่น เช่น ระดับทำนบดิน +18.000 หน้าอื่นก็ต้องเ็น ระดับ +18.000

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
    """สกัดและทำความสะอาด JSON อย่างแข็งแกร่ง ป้องกันโค้ดดิบหลุดไปแสดงผล"""
    if not text:
        return None
    try:
        # 1. ตัดข้อความเกริ่นนำ ดึงเฉพาะก้อน JSON ระหว่าง { ... }
        match = re.search(r'\{.*\}', text, re.DOTALL)
        json_str = match.group(0) if match else text
        
        # 2. ล้างตัวอักษรขยะและ Markdown Block
        json_str = re.sub(r'```json\s*', '', json_str)
        json_str = re.sub(r'```\s*', '', json_str).strip()
        
        data = json.loads(json_str)
        
        # ถ้ารับค่ามาเป็น List ให้ดึงเฉพาะ Element แรก
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            data = data[0]
            
        if isinstance(data, dict):
            issues = data.get("issues", [])
            if isinstance(issues, dict):
                data["issues"] = [issues]
            elif not isinstance(issues, list):
                data["issues"] = []
            return data
            
        return None
    except Exception:
        return None

def generate_export_filename(project_name="ฝายพุม่วงพร้อมอาคารประกอบ", extension="pdf"):
    """
    สร้างชื่อไฟล์ส่งออกอัตโนมัติในรูปแบบ: YYMMDD Verified-ชื่อโครงการ.ext
    ตัวอย่าง: 691109 Verified-ฝายพุม่วงพร้อมอาคารประกอบ.pdf
    """
    now = datetime.now()
    thai_year_short = str(now.year + 543)[-2:]  # แปลง ค.ศ. เป็น พ.ศ. ย่อ 2 หลัก
    date_str = f"{thai_year_short}{now.strftime('%m%d')}"  # ฟอร์แมต "691109"
    
    # ทำความสะอาดชื่อโครงการ (ตัดสัญลักษณ์ที่ไม่เหมาะกับชื่อไฟล์ออก)
    clean_project_name = re.sub(r'[\\/*?:"<>|]', "", str(project_name)).strip()
    if not clean_project_name or clean_project_name == "แบบแปลนวิศวกรรม":
        clean_project_name = "ฝายพุม่วงพร้อมอาคารประกอบ"
        
    return f"{date_str} Verified-{clean_project_name}.{extension}"

def process_page_with_dynamic_fallback(img):
    """ส่งคำขอไปยังโมเดลที่เปิดใช้งานจริง โดยตั้งค่า system_instruction แยก เพื่อป้องกัน AI ทวนคำสั่ง"""
    models_to_try = get_valid_vision_models()
    last_error = None
    
    prompt_user = "กรุณาตรวจสอบแบบแปลนวิศวกรรมนี้ตามคำสั่ง และส่งคืนผลลัพธ์เป็น JSON ภาษาไทยตามโครงสร้างที่กำหนดเท่านั้น"
    
    for model_name in models_to_try:
        try:
            # กำหนด system_instruction แยกจาก Prompt ป้องกันคำสั่งหลุดเข้าเนื้อหา
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt
            )
            response = model.generate_content(
                [prompt_user, img],
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
                    response = model.generate_content([prompt_user, img])
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
import random

def render_engineering_tips():
    """แสดงเกร็ดเกณฑ์มาตรฐานวิศวกรรมชลประทาน สลับกันให้อ่านระหว่างรอประมวลผล"""
    tips = [
        "💡 **เกณฑ์ฐานรากอาคาร:** ดินฐานรากต้องรับแรงกดปลอดภัยได้ไม่น้อยกว่า 15 ตัน/ตร.ม. โดยมี Factor of Safety (F.S.) ไม่น้อยกว่า 3",
        "💡 **ข้อกำหนดงานดินถม:** ดินถมบดอัดแน่นข้างโครงสร้างต้องบดอัดไม่น้อยกว่า 95% S.P.C.T. โดยความหนาแต่ละชั้นไม่เกิน 0.15 ม.",
        "💡 **วัสดุกันซึม Waterstop:** รอยต่อโครงสร้างคอนกรีตสัมผัสน้ำ (Construction/Expansion Joint) ต้องระบุชนิด Elastic Filler และ Rubber Waterstop ให้ชัดเจน",
        "💡 **กล่องลวดตาข่าย GABION:** ลวดถักตาข่ายต้องได้มาตรฐาน มอก.3050 เคลือบสังกะสีไม่น้อยกว่า 240-275 กรัม/ตร.ม. และผูกยึด 3 รอบสลับ 1 รอบ",
        "💡 **ระยะทาบเหล็กเสริม:** เหล็กข้ออ้อย (SD40) ทาบไม่น้อยกว่า 30 เท่าของเส้นผ่านศูนย์กลาง (30d) และเหล็กเส้นกลม (SR24) ทาบไม่น้อยกว่า 50d",
        "💡 **ชั้นคอนกรีตหุ้มเหล็ก (Covering):** ผิวคอนกรีตหล่อสัมผัสดินโดยตรงต้องมีระยะหุ้มเหล็กเสริมไม่น้อยกว่า 7.5 ซม."
    ]
    st.info(f"📐 **เกร็ดมาตรฐานวิศวกรรมชลประทาน (RID Standard Note):**\n\n{random.choice(tips)}")
    
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
        
        # กล่องสำหรับแสดงเกร็ดมาตรฐานชลประทานสลับไปมาระหว่างรอ
        tip_box = st.empty()
        
        # สร้างพื้นที่สำหรับแสดงผลลัพธ์ทีละแผ่นสดๆ (Live Incremental Section)
        st.markdown("### 🟢 ผลการตรวจสอบแบบแปลน (แสดงผลสดทีละแผ่น)")
        live_results_area = st.container()

        current_dpi = 150
        processed_count = 0
        total_selected = len(pages_to_process)
        out_pdf = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
        
        for idx, page_num in enumerate(pages_to_process):
            page_key = f"{uploaded_file.name}_page_{page_num}"
            
            # สุ่มเกร็ดมาตรฐานวิศวกรรมแสดงให้อ่านระหว่างรอแผ่นถัดไป
            with tip_box:
                render_engineering_tips()
                
            if page_key not in st.session_state.review_results:
                status_text.info(f"⏳ กำลังวิเคราะห์แผ่นที่ {page_num + 1} จากทั้งหมด {total_pages} แผ่น...")
                
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=current_dpi)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                
                try:
                    resp_text, used_model = process_page_with_dynamic_fallback(img)
                    result = clean_json_response(resp_text)
                    
                    if isinstance(result, dict):
                        result["used_model"] = used_model
                        st.session_state.review_results[page_key] = result
                    else:
                        clean_desc = re.sub(r'[\{\}\[\]"\\\\`]', '', resp_text)
                        clean_desc = re.sub(r'(drawing_number|sheet_number|project_type|issues|type|error_keyword|description|box_2d|summary):', '', clean_desc)
                        clean_desc = clean_desc.strip()[:300]
            
                        st.session_state.review_results[page_key] = {
                            "drawing_number": "สชป.14-1-XXXX",
                            "sheet_number": f"แผ่นที่ {page_num + 1}",
                            "project_type": "แบบแปลนวิศวกรรม",
                            "used_model": used_model,
                            "issues": [{
                                "type": "Observation", 
                                "error_keyword": "ตรวจสอบภาพรวม", 
                                "description": f"ผลการวิเคราะห์ภาพรวม: {clean_desc if clean_desc else 'ตรวจพบข้อสังเกตบนแบบแปลน กรุณาตรวจสอบรายละเอียดในสนาม'}", 
                                "box_2d": [0,0,0,0]
                            }]
                        }
                    
                    del pix
                    del img
                    gc.collect()
                    
                except Exception as e:
                    st.error(f"❌ แผ่นที่ {page_num + 1} เกิดข้อผิดพลาด: {e}")
                    continue

            # --- แสดงผลลัพธ์ของแผ่นที่พึ่งตรวจเสร็จทันที (Incremental Render) ---
            result_json = st.session_state.review_results[page_key]
            dwg_no = result_json.get('drawing_number', 'ไม่ระบุ')
            sheet_no = result_json.get('sheet_number', f'แผ่นที่ {page_num + 1}')
            title = result_json.get('project_type', 'แบบแปลนวิศวกรรม')
            model_used = result_json.get('used_model', 'ไม่ระบุ')

            with live_results_area:
                with st.expander(f"📄 [{dwg_no}] {sheet_no} : {title} (ตรวจเสร็จแล้ว)", expanded=True):
                    live_page = out_pdf.load_page(page_num)
                    draw_spatial_guided_annotation(live_page, result_json.get("issues", []))
                    
                    pix_annotated = live_page.get_pixmap(dpi=120)
                    img_annotated = Image.open(io.BytesIO(pix_annotated.tobytes("png")))
                    st.image(img_annotated, use_container_width=True)
                    
                    issues_this_page = result_json.get("issues", [])
                    if isinstance(issues_this_page, list) and issues_this_page:
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

            processed_count += 1
            progress_bar.progress(processed_count / total_selected)
            time.sleep(10) # หน่วงเวลาตามมาตรฐานโควต้าฟรี
        
        tip_box.empty() # ล้างกล่องเกร็ดความรู้เมื่อตรวจเสร็จครบทั้งหมด
        status_text.success(f"✅ ตรวจสอบเสร็จสิ้นทั้งหมด {len(st.session_state.review_results)} แผ่น!")

    if st.session_state.review_results:
        st.markdown("---")
        
        # --- 1. สกัดชื่อโครงการจากแผ่นแรกเพื่อนำมาตั้งชื่อไฟล์ส่งออก ---
        first_page_key = next(iter(st.session_state.review_results), None)
        detected_project_name = "ฝายพุม่วงพร้อมอาคารประกอบ"
        if first_page_key and isinstance(st.session_state.review_results[first_page_key], dict):
            p_name = st.session_state.review_results[first_page_key].get("project_type", "")
            if p_name and p_name != "แบบแปลนวิศวกรรม":
                detected_project_name = p_name

        # --- 2. สรุปสถิติภาพรวม (Dashboard Cards) ---
        total_issues_count = 0
        typo_count = 0
        eng_count = 0
        obs_count = 0
        excel_rows = []

        for page_key, result_json in st.session_state.review_results.items():
            if isinstance(result_json, dict):
                dwg_no = result_json.get('drawing_number', '-')
                sheet_no = result_json.get('sheet_number', '-')
                p_title = result_json.get('project_type', '-')
                
                issues = result_json.get("issues", [])
                total_issues_count += len(issues)
                
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    i_type = str(issue.get("type", "Observation"))
                    kw = str(issue.get("error_keyword", "-"))
                    desc = str(issue.get("description", "-"))
                    
                    if i_type.lower() == "typo":
                        typo_count += 1
                    elif i_type.lower() == "engineering":
                        eng_count += 1
                    else:
                        obs_count += 1
                        
                    excel_rows.append({
                        "หมายเลขแบบ": dwg_no,
                        "แผ่นที่": sheet_no,
                        "ชื่อแบบ/รายการ": p_title,
                        "ประเภทข้อสังเกต": i_type,
                        "คำผิด/จุดสังเกต": kw,
                        "รายละเอียดข้อแนะนำทางวิศวกรรม": desc
                    })

        st.markdown("## 📊 สรุปภาพรวมผลการตรวจสอบแบบแปลน")
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("จำนวนแผ่นที่ตรวจ", f"{len(st.session_state.review_results)} / {total_pages} แผ่น")
        m_col2.metric("ข้อสังเกตทางวิศวกรรม 📐", f"{eng_count} จุด")
        m_col3.metric("คำผิด / ฟอนต์เพี้ยน 🔤", f"{typo_count} จุด")
        m_col4.metric("ข้อสังเกตงานสนาม 💡", f"{obs_count} จุด")

        st.markdown("---")
        
        # --- 3. ประมวลผลวาดวงกลมแดงและเตรียมไฟล์ส่งออก ---
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
        
        # --- 4. สร้างชื่อไฟล์ตามวันที่และชื่อโครงการอัตโนมัติ ---
        export_pdf_filename = generate_export_filename(detected_project_name, "pdf")
        export_excel_filename = generate_export_filename(detected_project_name, "xlsx")
        
        st.markdown("### 📥 ดาวน์โหลดเอกสารรายงานการตรวจแบบ")
        dl_col1, dl_col2 = st.columns(2)
        
        with dl_col1:
            st.download_button(
                label=f"📄 ดาวน์โหลด PDF สรุปการตรวจทุกแผ่น ({export_pdf_filename})",
                data=out_pdf_bytes.getvalue(),
                file_name=export_pdf_filename,
                mime="application/pdf"
            )
            
        with dl_col2:
            if excel_rows:
                df_export = pd.DataFrame(excel_rows)
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    df_export.to_excel(writer, index=False, sheet_name='รายการตรวจแบบ')
                
                st.download_button(
                    label=f"📊 ดาวน์โหลด ตารางสรุปรายการตรวจแบบ (.xlsx)",
                    data=excel_buffer.getvalue(),
                    file_name=export_excel_filename,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
