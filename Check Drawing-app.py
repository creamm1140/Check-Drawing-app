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

# ตั้งค่าการเก็บ Cache ผลลัพธ์ใน Session State เพื่อประหยัดโควต้า API
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

def get_active_model_name():
    """ดึงชื่อโมเดลฟรีที่พร้อมใช้งานจริงในระบบ"""
    preferred_models = [
        'gemini-1.5-flash',
        'gemini-1.5-pro-latest',
        'gemini-1.5-pro'
    ]
    try:
        available = [
            m.name.replace('models/', '') 
            for m in genai.list_models() 
            if 'generateContent' in m.supported_generation_methods
        ]
        for m in preferred_models:
            if m in available:
                return m
        return available[0] if available else 'gemini-1.5-flash'
    except Exception:
        return 'gemini-1.5-flash'

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

def draw_spatial_guided_annotation(page, issues):
    """ระบบวาดวงกลมแบบ Spatial Filtering จำกัดพื้นที่ค้นหาป้องกันการวงผิดตำแหน่ง"""
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
    
    st.sidebar.header("⚙️ การตั้งค่าประหยัดโควต้า (Free Tier)")
    
    # ตัวเลือกกำหนดหน้าที่จะตรวจ เพื่อไม่ให้เปลือง Request
    mode = st.sidebar.radio("รูปแบบการตรวจ:", ["เลือกเฉพาะบางหน้า (แนะนำสำหรับ Demo)", "ตรวจทุกหน้า"])
    
    pages_to_process = []
    if mode == "เลือกเฉพาะบางหน้า (แนะนำสำหรับ Demo)":
        selected_pages = st.sidebar.multiselect(
            "เลือกหน้าที่ต้องการตรวจ:", 
            options=list(range(1, total_pages + 1)),
            default=[1, 4, 5] if total_pages >= 5 else [1]
        )
        pages_to_process = [p - 1 for p in selected_pages]
    else:
        pages_to_process = list(range(total_pages))
        
    model_name = get_active_model_name()
    st.sidebar.info(f"🤖 โมเดลที่ใช้: {model_name}")

    if st.button("🚀 เริ่มการวิเคราะห์แบบแปลน"):
        status_text = st.empty()
        model = genai.GenerativeModel(model_name)
        current_dpi = 200  # ปรับความคมชัดสูงสุดเพื่อให้ AI อ่านสระและมิติตัวเลขได้ชัดเจน
        
        for idx, page_num in enumerate(pages_to_process):
            page_key = f"{uploaded_file.name}_page_{page_num}"
            
            # เช็ก Cache ถ้าเคยตรวจแล้วจะไม่เรียก API ซ้ำ
            if page_key not in st.session_state.review_results:
                status_text.info(f"⏳ กำลังส่ง AI วิเคราะห์หน้า {page_num + 1} จากทั้งหมด {total_pages} หน้า...")
                
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=current_dpi)
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                
                try:
                    response = model.generate_content(
                        [system_prompt, img],
                        generation_config=genai.GenerationConfig(
                            response_mime_type="application/json",
                            temperature=0.0,
                            top_k=1,
                            top_p=0.1,
                        ),
                    )
                    st.session_state.review_results[page_key] = clean_json_response(response.text)
                    del pix
                    del img
                    gc.collect()
                    time.sleep(4)  # ชะลอ 4 วินาทีเพื่อป้องกันติด Rate Limit ต่อนาที
                except Exception as e:
                    st.error(f"❌ หน้า {page_num + 1} ติดปัญหา API: {e}")
                    break
        
        status_text.success("✅ ประมวลผลเสร็จสิ้น!")

    # การแสดงผลลัพธ์
    if st.session_state.review_results:
        st.markdown("## 📊 ผลการตรวจสอบแบบแปลน")
        out_pdf = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
        
        for page_num in pages_to_process:
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
        
        out_pdf_bytes = io.BytesIO()
        out_pdf.save(out_pdf_bytes)
        
        st.download_button(
            label="📥 ดาวน์โหลดไฟล์ PDF (พร้อมวงกลมแดงและเลขแบบ)",
            data=out_pdf_bytes.getvalue(),
            file_name="Reviewed_Engineering_Drawing_Demo.pdf",
            mime="application/pdf"
        )
