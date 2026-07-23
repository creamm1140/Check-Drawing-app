import streamlit as st
import fitz  # PyMuPDF สำหรับจัดการไฟล์ PDF
from PIL import Image, ImageDraw # เพิ่ม ImageDraw สำหรับวาดวงกลม
import io

st.set_page_config(page_title="Hydraulic & Structural Review", layout="wide")

st.title("ระบบผู้ช่วยตรวจแบบ: วิศวกรรมชลประทานและอุทกวิทยา")
st.markdown("อัปโหลดไฟล์ (PDF, PNG, JPG) เพื่อตรวจสอบความสอดคล้องและข้อกำหนดทางวิศวกรรม")

# 1. ปรับให้อัปโหลดได้ทั้ง PDF และรูปภาพ
uploaded_file = st.file_uploader("เลือกไฟล์แบบแปลนของคุณ", type=["pdf", "png", "jpg", "jpeg"])

# --- ฟังก์ชันจำลองการวาดวงกลมสีแดง ---
def mark_errors_on_image(image):
    # สร้างออบเจกต์สำหรับวาดรูป
    draw = ImageDraw.Draw(image)
    
    # ดึงขนาดความกว้าง ความสูง ของรูปภาพ
    width, height = image.size
    
    # จำลองค่าพิกัด (x0, y0, x1, y1) ที่ AI ตรวจพบว่ามีข้อผิดพลาด
    # ตัวอย่าง: ตีวงกลมบริเวณตำแหน่งกึ่งกลางค่อนไปทางซ้ายบนของแบบ
    box1 = [width * 0.2, height * 0.3, width * 0.35, height * 0.45] 
    
    # วาดวงรี (วงกลม) สีแดง เส้นหนา 5 พิกเซล
    draw.ellipse(box1, outline="red", width=5)
    
    # สามารถเพิ่มวงกลมจุดอื่นได้
    # box2 = [width * 0.6, height * 0.7, width * 0.8, height * 0.8]
    # draw.ellipse(box2, outline="red", width=5)
    
    return image
# ---------------------------------

if uploaded_file is not None:
    st.info("กำลังประมวลผลไฟล์และจำลองการวิเคราะห์...")
    
    # ดึงนามสกุลไฟล์มาเช็คว่าเป็น PDF หรือ รูปภาพ
    file_extension = uploaded_file.name.split('.')[-1].lower()
    
    # กรณีที่ 1: ผู้ใช้อัปโหลดไฟล์รูปภาพ (PNG, JPG)
    if file_extension in ["png", "jpg", "jpeg"]:
        # เปิดไฟล์รูปภาพโดยตรง
        original_image = Image.open(uploaded_file).convert("RGB")
        
        # นำรูปไปวาดวงกลมสีแดง
        marked_image = mark_errors_on_image(original_image)
        
        st.subheader("ผลการตรวจแบบ (รูปภาพ)")
        st.image(marked_image, use_column_width=True)
        
        with st.expander("📝 ผลการตรวจสอบและข้อเสนอแนะ", expanded=True):
            st.markdown("""
            **🔍 จุดที่ 1 (วงกลมสีแดง): การตรวจสอบความสอดคล้อง (Consistency)**
            * **ปัญหาที่พบ:** ตัวเลขระยะ Elev. ในรูปตัด (Cross-section) ไม่ตรงกับรายการคำนวณด้านล่าง
            * **ข้อเสนอแนะ:** กรุณาตรวจสอบระดับสันฝาย (Crest Elevation) อีกครั้ง

            **💧 จุดที่ 2: ข้อเสนอแนะด้านวิศวกรรมอุทกวิทยา (Hydrology)**
            * **ปัญหาที่พบ:** มีการสะกดคำผิดจาก "Uplift Pressure" เป็น "Uplif Pressure"
            """, unsafe_allow_html=True)
            
    # กรณีที่ 2: ผู้ใช้อัปโหลดไฟล์ PDF
    elif file_extension == "pdf":
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            img_data = pix.tobytes("png")
            original_image = Image.open(io.BytesIO(img_data)).convert("RGB")
            
            # นำรูปแต่ละหน้าไปวาดวงกลมสีแดง
            marked_image = mark_errors_on_image(original_image)
            
            st.subheader(f"หน้า {page_num + 1}")
            st.image(marked_image, use_column_width=True)
            
            with st.expander(f"📝 ผลการตรวจสอบและข้อเสนอแนะ (หน้า {page_num + 1})", expanded=True):
                st.markdown("""
                **🔍 จุดที่ 1 (วงกลมสีแดง): การออกแบบ Spillway**
                * **ปัญหาที่พบ:** ค่าสัมประสิทธิ์การระบายน้ำ (C) ที่ใช้ในสมการ ค่อนข้างสูงเมื่อเทียบกับสัดส่วนความยาวสันฝายที่ปรากฏ
                * **ข้อเสนอแนะ:** แนะนำให้ใช้ค่า C = 1.7 - 2.0 ตามมาตรฐานของกรมชลประทาน สำหรับฝายสันกว้าง (Broad-crested weir)
                """, unsafe_allow_html=True)
