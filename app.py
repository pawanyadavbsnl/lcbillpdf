
import streamlit as st
import fitz
import re
import io
import zipfile
from datetime import datetime

# =====================================================
# PAGE CONFIGURATION
# =====================================================

st.set_page_config(
    page_title="BSNL Bulk Invoice Editor",
    page_icon="📄",
    layout="wide"
)

st.title("BSNL Bulk Invoice Editor")
st.write("Bulk editing of BSNL Leased Circuit PDF Invoices")

st.info(
    "Upload multiple BSNL PDF invoices. "
    "The application will update the amount fields "
    "using Total Current Charges."
)

# =====================================================
# INDIAN NUMBER TO WORDS
# =====================================================

ONES = [
    "Zero", "One", "Two", "Three", "Four",
    "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen",
    "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen"
]

TENS = [
    "", "", "Twenty", "Thirty", "Forty",
    "Fifty", "Sixty", "Seventy",
    "Eighty", "Ninety"
]


def under_thousand(n):

    result = []

    if n >= 100:
        result.append(ONES[n // 100])
        result.append("Hundred")
        n %= 100

    if n >= 20:
        result.append(TENS[n // 10])
        n %= 10

    if n > 0:
        result.append(ONES[n])

    return " ".join(result)


def number_to_words(number):

    n = int(round(number))

    if n == 0:
        return "Zero Only"

    parts = []

    for value, label in [
        (10000000, "Crore"),
        (100000, "Lakh"),
        (1000, "Thousand"),
        (1, "")
    ]:

        q, n = divmod(n, value)

        if q:
            word = under_thousand(q)

            if label:
                word += " " + label

            parts.append(word)

    return " ".join(parts) + " Only"
total = find_total_current_charges(page)

# =====================================================
# FIND TOTAL CURRENT CHARGES
# =====================================================

def find_total_current_charges(page):

    words = page.get_text("words")

    label_rects = page.search_for(
        "Total Current Charges"
    )

    if not label_rects:
        raise ValueError(
            "Total Current Charges label not found. "
            "Please check invoice format."
        )

    label = label_rects[0]

    candidates = []

    for word in words:

        rect = fitz.Rect(word[:4])

        if not re.fullmatch(
            r"[\d,]+\.\d{2}", word[4]
        ):
            continue

        if (
            rect.x0 >= label.x1 - 2
            and abs(rect.y0 - label.y0) < 10
        ):
            candidates.append((rect, word[4]))

    if not candidates:
        raise ValueError(
            "Total Current Charges amount could not be read."
        )

    candidates.sort(key=lambda x: x[0].x0)

    return float(
        candidates[0][1].replace(",", "")
    )


# =====================================================
# FIND AMOUNT INSIDE SPECIFIED AREA
# =====================================================

def find_amount_in_area(page, area, used):

    area = fitz.Rect(area)

    candidates = []

    for word in page.get_text("words"):

        rect = fitz.Rect(word[:4])

        if not re.fullmatch(
            r"[\d,]+\.\d{2}", word[4]
        ):
            continue

        if not area.contains(rect):
            continue

        if any(rect.intersects(x) for x in used):
            continue

        candidates.append(rect)

    if not candidates:
        return None

    # Select the amount closest to the right side.
    candidates.sort(
        key=lambda r: (r.y0, -r.x0)
    )

    return candidates[0]


# =====================================================
# AMOUNT IN WORDS AREA
# =====================================================

def find_words_area(page):

    labels = page.search_for(
        "Amount in Words:"
    )

    if not labels:
        raise ValueError(
            "Amount in Words label not found."
        )

    label = labels[0]

    # Original words may occupy two lines.
    # Keep the correction inside the header's available space.
    return fitz.Rect(
        label.x1 + 2,
        label.y0 - 2,
        page.rect.width - 35,
        label.y0 + 25
    )


# =====================================================
# EDIT PDF
# =====================================================

def edit_invoice(pdf_bytes):

    doc = fitz.open(
        stream=pdf_bytes,
        filetype="pdf"
    )

    if len(doc) == 0:
        raise ValueError("Empty PDF.")

    page = doc[0]

    # Read Total Current Charges.
    total = find_total_current_charges(page)

    amount = f"{total:.2f}"
    rupee_amount = f"₹ {amount}"

    words_amount = (
        "Rupees " + number_to_words(total)
    )

    # -------------------------------------------------
    # Define the five amount locations.
    # Coordinates are based on the supplied BSNL
    # invoice template (page 1).
    # -------------------------------------------------

    target_areas = {

        # 1. Top right AMOUNT PAYABLE
        "Header Amount Payable":
            (450, 120, 575, 160),

        # 2. Account Summary - Account Balance
        "Account Balance":
            (400, 245, 480, 270),

        # 3. Account Summary - Amount Payable
        "Summary Amount Payable":
            (480, 235, 570, 260),

        # 4. Payment slip cheque amount
        "Payment Slip Amount":
            (215, 725, 280, 750),

        # 5. Payment slip right side Amount Payable
        "Payment Slip Amount Payable":
            (465, 765, 550, 790)
    }

    used = []
    patches = []

    # -------------------------------------------------
    # Find all five amounts.
    # -------------------------------------------------

    for field, area in target_areas.items():

        rect = find_amount_in_area(
            page,
            area,
            used
        )

        if rect is None:

            raise ValueError(
                f"Could not locate {field}. "
                "Invoice layout may be different."
            )

        patches.append(
            (rect, rupee_amount, 8)
        )

        used.append(rect)

    # -------------------------------------------------
    # Previous Balance = ZERO
    # -------------------------------------------------

    previous_rect = find_amount_in_area(
        page,
        (15, 245, 100, 270),
        used
    )

    if previous_rect is None:
        raise ValueError(
            "Previous Balance amount not found."
        )

    patches.append(
        (previous_rect, "₹ 0.00", 8)
    )

    used.append(previous_rect)

    # -------------------------------------------------
    # Last Payment = ZERO
    # -------------------------------------------------

    last_payment_rect = find_amount_in_area(
        page,
        (110, 245, 185, 270),
        used
    )

    if last_payment_rect is None:
        raise ValueError(
            "Last Payment amount not found."
        )

    patches.append(
        (last_payment_rect, "₹ 0.00", 8)
    )

    used.append(last_payment_rect)

    # -------------------------------------------------
    # Amount in Words
    # -------------------------------------------------

    word_rect = find_words_area(page)

    patches.append(
        (word_rect, words_amount, 8)
    )

    # -------------------------------------------------
    # Apply redactions
    # -------------------------------------------------

    for rect, new_text, fontsize in patches:

        page.add_redact_annot(
            rect,
            fill=(1, 1, 1)
        )

    page.apply_redactions(
        images=0,
        graphics=0
    )

    # -------------------------------------------------
    # Insert revised text
    # -------------------------------------------------

    for rect, new_text, fontsize in patches:

        is_amount = new_text.startswith("₹")

        result = page.insert_textbox(
            rect,
            new_text,
            fontsize=fontsize,
            fontname="helv",
            color=(0, 0, 0),
            align=(
                fitz.TEXT_ALIGN_RIGHT
                if is_amount
                else fitz.TEXT_ALIGN_LEFT
            )
        )

        if result < 0:
            raise ValueError(
                "Text does not fit in the invoice field: "
                + new_text
            )

    # -------------------------------------------------
    # Save revised PDF in memory
    # -------------------------------------------------

    output = doc.tobytes(
        garbage=4,
        deflate=True
    )

    doc.close()

    return output, total, words_amount


# =====================================================
# STREAMLIT USER INTERFACE
# =====================================================

st.divider()

st.subheader("Upload Invoice PDFs")

uploaded_files = st.file_uploader(
    "Select multiple BSNL invoice PDF files",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:

    st.write(
        f"Total invoices selected: {len(uploaded_files)}"
    )

    if st.button(
        "PROCESS ALL INVOICES",
        type="primary",
        use_container_width=True
    ):

        zip_buffer = io.BytesIO()

        success_count = 0
        failed_files = []
        report = []

        progress = st.progress(0)

        with zipfile.ZipFile(
            zip_buffer,
            "w",
            zipfile.ZIP_DEFLATED
        ) as zip_file:

            total_files = len(uploaded_files)

            for index, uploaded in enumerate(
                uploaded_files
            ):

                try:

                    revised_pdf, total, words = (
                        edit_invoice(
                            uploaded.getvalue()
                        )
                    )

                    filename = (
                        uploaded.name.rsplit(".", 1)[0]
                        + "_revised.pdf"
                    )

                    zip_file.writestr(
                        filename,
                        revised_pdf
                    )

                    success_count += 1

                    report.append({
                        "Invoice": uploaded.name,
                        "Total Current Charges":
                            f"₹ {total:,.2f}",
                        "Amount in Words": words,
                        "Status": "Successful"
                    })

                except Exception as error:

                    failed_files.append({
                        "Invoice": uploaded.name,
                        "Error": str(error)
                    })

                progress.progress(
                    (index + 1) / total_files
                )

        # -------------------------------------------------
        # RESULTS
        # -------------------------------------------------

        st.divider()

        st.subheader("Processing Summary")

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Total Invoices",
            len(uploaded_files)
        )

        col2.metric(
            "Successful",
            success_count
        )

        col3.metric(
            "Failed",
            len(failed_files)
        )

        if report:

            st.success(
                "Invoice processing completed."
            )

            st.dataframe(
                report,
                use_container_width=True
            )

            st.download_button(
                label="DOWNLOAD ALL REVISED INVOICES",
                data=zip_buffer.getvalue(),
                file_name="BSNL_Revised_Invoices.zip",
                mime="application/zip",
                type="primary",
                use_container_width=True
            )

        if failed_files:

            st.error(
                "Some invoices could not be processed."
            )

            st.dataframe(
                failed_files,
                use_container_width=True
            )

st.divider()

st.subheader("Correction Rules")

st.table({
    "Invoice Field": [
        "Amount Payable - Header",
        "Account Balance",
        "Amount Payable - Summary",
        "Payment Slip Amount",
        "Payment Slip Amount Payable",
        "Previous Balance",
        "Last Payment",
        "Amount in Words"
    ],

    "New Value": [
        "Total Current Charges",
        "Total Current Charges",
        "Total Current Charges",
        "Total Current Charges",
        "Total Current Charges",
        "₹ 0.00",
        "₹ 0.00",
        "Automatically converted"
    ]
})

st.caption(
    "BSNL Bulk Invoice Editor | PDF processing utility"
)

st.warning(
    "Important: This program is designed for the supplied "
    "BSNL invoice layout. It edits page 1 only. It does not "
    "regenerate IRN, QR codes, or digitally signed invoice "
    "content. Test on copies and verify every output before "
    "any official use. Do not use it to alter issued tax "
    "invoices without authorized approval."
)
