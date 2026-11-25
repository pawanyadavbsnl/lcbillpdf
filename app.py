
import streamlit as st
import fitz
import re
import io
import zipfile

# =========================================================
# PAGE SETTINGS
# =========================================================

st.set_page_config(
    page_title="BSNL Bulk Invoice Editor",
    page_icon="📄",
    layout="wide"
)

st.title("BSNL Bulk Invoice Editor")
st.write("Bulk editing of BSNL Leased Circuit PDF Invoices")

st.info(
    "Upload multiple BSNL PDF invoices. "
    "The application will automatically read Total Current Charges "
    "and update the specified invoice fields."
)

# =========================================================
# INDIAN NUMBER TO WORDS
# =========================================================

ONES = [
    "Zero", "One", "Two", "Three", "Four",
    "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen",
    "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen"
]

TENS = [
    "", "", "Twenty", "Thirty", "Forty",
    "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"
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


# =========================================================
# FIND TOTAL CURRENT CHARGES
# FIXED: READS WORDS FROM THE SAME PDF BASELINE
# =========================================================

def find_total_current_charges(page):

    words = page.get_text("words")

    # Locate the word Total
    for word in words:

        if word[4].strip().lower() != "total":
            continue

        y = word[1]

        # Get words on the same horizontal line
        line_words = [
            w for w in words
            if abs(w[1] - y) <= 3
        ]

        line_words.sort(key=lambda x: x[0])

        line_text = " ".join(
            w[4] for w in line_words
        )

        if re.search(
            r"Total\s+Current\s+Charges",
            line_text,
            re.IGNORECASE
        ):

            # Find amount on the same line
            amounts = []

            for w in line_words:

                if w[0] < word[0]:
                    continue

                if re.fullmatch(
                    r"[\d,]+\.\d{2}", w[4]
                ):
                    amounts.append(w[4])

            if amounts:

                return float(
                    amounts[-1].replace(",", "")
                )

    # Fallback: use text blocks
    blocks = page.get_text("blocks")

    for block in blocks:

        text = block[4]

        if "Total Current Charges" in text:

            numbers = re.findall(
                r"[\d,]+\.\d{2}",
                text
            )

            if numbers:

                return float(
                    numbers[-1].replace(",", "")
                )

    raise ValueError(
        "Total Current Charges amount could not be read. "
        "Check whether the PDF contains selectable text."
    )


# =========================================================
# INVOICE FIELD COORDINATES
# BASED ON YOUR UPLOADED BSNL INVOICE
# =========================================================

BASE_WIDTH = 595.276
BASE_HEIGHT = 841.890


def scaled_rect(page, coordinates):

    x0, y0, x1, y1 = coordinates

    sx = page.rect.width / BASE_WIDTH
    sy = page.rect.height / BASE_HEIGHT

    return fitz.Rect(
        x0 * sx,
        y0 * sy,
        x1 * sx,
        y1 * sy
    )


# =========================================================
# PREPARE INVOICE CORRECTIONS
# =========================================================

def edit_invoice(pdf_bytes):

    doc = fitz.open(
        stream=pdf_bytes,
        filetype="pdf"
    )

    if len(doc) == 0:
        raise ValueError("Invalid PDF file.")

    page = doc[0]

    # -----------------------------------------------------
    # READ TOTAL CURRENT CHARGES
    # -----------------------------------------------------

    total = find_total_current_charges(page)

    amount = f"{total:.2f}"

    rupee_amount = "₹ " + amount

    amount_words = (
        "Rupees " + number_to_words(total)
    )

    # -----------------------------------------------------
    # EXACT AMOUNT FIELD LOCATIONS
    # -----------------------------------------------------

    corrections = [

        # 1. Header Amount Payable
        (
            (476, 134, 535, 148),
            rupee_amount,
            7.5
        ),

        # 2. Account Balance
        (
            (405, 252, 453, 265),
            rupee_amount,
            7.5
        ),

        # 3. Account Summary Amount Payable
        # Excludes the Rounded Up text below.
        (
            (508, 244, 557, 254),
            rupee_amount,
            7.0
        ),

        # 4. Payment slip cheque amount
        (
            (220, 734, 270, 747),
            rupee_amount,
            7.5
        ),

        # 5. Payment slip Amount Payable
        (
            (470, 772, 522, 785),
            rupee_amount,
            7.5
        ),

        # 6. Previous Balance = ZERO
        (
            (25, 253, 69, 265),
            "₹ 0.00",
            7.5
        ),

        # 7. Last Payment = ZERO
        (
            (119, 253, 145, 265),
            "₹ 0.00",
            7.5
        )
    ]

    patches = []

    # Convert coordinates according to PDF page size
    for coordinates, new_text, fontsize in corrections:

        rect = scaled_rect(
            page,
            coordinates
        )

        patches.append(
            (rect, new_text, fontsize)
        )

    # -----------------------------------------------------
    # AMOUNT IN WORDS
    # -----------------------------------------------------

    words_rect = scaled_rect(
        page,
        (349, 57, 552, 82)
    )

    patches.append(
        (
            words_rect,
            amount_words,
            7.5
        )
    )

    # -----------------------------------------------------
    # APPLY ALL REDACTIONS
    # -----------------------------------------------------

    for rect, new_text, fontsize in patches:

        page.add_redact_annot(
            rect,
            fill=(1, 1, 1)
        )

    page.apply_redactions(
        images=0,
        graphics=0
    )

    # -----------------------------------------------------
    # INSERT UPDATED VALUES
    # -----------------------------------------------------

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
                "Text could not fit in the invoice field: "
                + new_text
            )

    # -----------------------------------------------------
    # SAVE REVISED PDF
    # -----------------------------------------------------

    output = doc.tobytes(
        garbage=4,
        deflate=True
    )

    doc.close()

    return output, total, amount_words


# =========================================================
# STREAMLIT INTERFACE
# =========================================================

st.divider()

st.subheader("Upload BSNL Invoice PDFs")

uploaded_files = st.file_uploader(
    "Choose multiple PDF invoices",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:

    st.success(
        f"{len(uploaded_files)} invoice(s) selected."
    )

    if st.button(
        "PROCESS ALL INVOICES",
        type="primary",
        use_container_width=True
    ):

        zip_buffer = io.BytesIO()

        successful = 0

        failed = []

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

                    revised_pdf, total, words = edit_invoice(
                        uploaded.getvalue()
                    )

                    filename = (
                        uploaded.name.rsplit(".", 1)[0]
                        + "_revised.pdf"
                    )

                    zip_file.writestr(
                        filename,
                        revised_pdf
                    )

                    successful += 1

                    report.append({
                        "Invoice Name": uploaded.name,
                        "Total Current Charges":
                            f"₹ {total:,.2f}",
                        "Amount in Words": words,
                        "Status": "Successful"
                    })

                except Exception as error:

                    failed.append({
                        "Invoice Name": uploaded.name,
                        "Error": str(error)
                    })

                progress.progress(
                    (index + 1) / total_files
                )

        # -------------------------------------------------
        # PROCESSING REPORT
        # -------------------------------------------------

        st.divider()

        st.subheader("Processing Results")

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Total Invoices",
            len(uploaded_files)
        )

        col2.metric(
            "Successfully Processed",
            successful
        )

        col3.metric(
            "Failed",
            len(failed)
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
                label="DOWNLOAD ALL REVISED INVOICES (ZIP)",
                data=zip_buffer.getvalue(),
                file_name="BSNL_Revised_Invoices.zip",
                mime="application/zip",
                type="primary",
                use_container_width=True
            )

        if failed:

            st.error(
                "The following invoices could not be processed:"
            )

            st.dataframe(
                failed,
                use_container_width=True
            )


# =========================================================
# CORRECTION RULES
# =========================================================

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

    "Updated Value": [

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
    "BSNL Bulk Invoice Editor | PDF Processing Utility"
)

st.warning(
    "This application is designed for the supplied BSNL "
    "invoice layout. It edits page 1 only. It does not "
    "regenerate IRN, QR codes, or digital signatures. "
    "Test all outputs before official use and obtain "
    "appropriate authorization for invoice corrections."
)
