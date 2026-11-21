"""E-mail messages sent by Train2Conquer (layout only - no sending, no database).

The OTP message is built here so that main.py only has to hand it to the mail
server. It has no Flask or network dependencies, so it can be tested and
previewed on its own.

Design rules for the HTML (so it works in Gmail, Outlook, Apple Mail, ...):
  * layout uses <table role="presentation"> and INLINE styles - the <style>
    block in <head> only adds small-screen tweaks and is safe to be ignored;
  * system fonts only (no web fonts), no background images, no JavaScript;
  * light background and dark text, with the Train2Conquer orange/gold used
    sparingly as accents;
  * the logo is attached to the message itself (Content-ID) instead of being
    linked, so it shows without any public URL and without tracking;
  * a plain-text version is always included.

The verification code is only ever placed in the message body. It is never
logged and never put in the subject line (subjects show up in lock-screen
notifications and server logs).
"""
import html
import os
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

BRAND = "Train2Conquer"
TAGLINE = "Your home workout & fitness companion"
LOGO_CID = "t2c-mark"
_LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "static", "assets", "img", "email-mark.png")

# Colours (kept in one place; they match the site's palette).
INK = "#15120F"          # headings and the code
BODY = "#3D3A36"         # paragraph text
MUTED = "#6F6B65"        # footer text
FLAME = "#FF4B26"        # brand orange
GOLD = "#D9A83B"         # brand gold
PAGE_BG = "#F4F3EF"
CARD_BORDER = "#E7E4DD"
CODE_BG = "#FFF5EE"
CODE_BORDER = "#FFD8BF"
NOTICE_BG = "#FAF8F3"


def _logo_bytes():
    try:
        with open(_LOGO_PATH, "rb") as handle:
            return handle.read()
    except OSError:
        return None            # the wordmark is real text, so the e-mail still looks right without it


def _minutes(seconds):
    return max(1, round(seconds / 60))


def otp_text(code, expires_minutes):
    """Plain-text version (shown by text-only clients and used as the fallback)."""
    return (
        f"{BRAND} - Verify your email address\n"
        "\n"
        f"We received a request to verify this email address for your {BRAND} account.\n"
        "Enter this verification code to continue:\n"
        "\n"
        f"    {code}\n"
        "\n"
        f"This code expires in {expires_minutes} minutes and can only be used once.\n"
        "\n"
        "SECURITY NOTICE: Never share your verification code with anyone.\n"
        f"{BRAND} will never ask you for it by email, phone or chat.\n"
        "\n"
        "If you didn't request this code, you can safely ignore this email. "
        "No changes were made to any account.\n"
        "\n"
        "--\n"
        f"{BRAND} - {TAGLINE}\n"
        "This is an automated message, please do not reply.\n"
    )


def otp_html(code, expires_minutes, recipient, has_logo=True):
    """HTML version. `code` is digits we generated; `recipient` is escaped before use."""
    code = html.escape(str(code))
    to = html.escape(recipient or "")
    year = datetime.now(timezone.utc).year
    logo = (f'<img src="cid:{LOGO_CID}" width="32" height="32" alt="" '
            'style="display:block;border:0;outline:none;text-decoration:none;width:32px;height:32px;">'
            if has_logo else "")
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    mono = "'SF Mono',SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
    preheader = f"Use this code to verify your email. It expires in {expires_minutes} minutes."

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>Verify your email address - {BRAND}</title>
<style>
  @media only screen and (max-width: 480px) {{
    .t2c-card {{ padding: 28px 20px !important; }}
    .t2c-code {{ font-size: 30px !important; letter-spacing: 6px !important; }}
    .t2c-h1 {{ font-size: 22px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:{PAGE_BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;mso-hide:all;">{html.escape(preheader)}&#8199;&#847;&#8199;&#847;&#8199;&#847;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{PAGE_BG};">
<tr><td align="center" style="padding:32px 12px;">

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;">
    <tr><td style="padding:0 4px 18px 4px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="vertical-align:middle;padding-right:10px;">{logo}</td>
        <td style="vertical-align:middle;font-family:{font};font-size:18px;font-weight:800;letter-spacing:1px;color:{INK};">TRAIN2<span style="color:{FLAME};">CONQUER</span></td>
      </tr></table>
    </td></tr>

    <tr><td class="t2c-card" style="background-color:#FFFFFF;border:1px solid {CARD_BORDER};border-top:4px solid {FLAME};border-radius:12px;padding:36px 36px 32px 36px;font-family:{font};">
      <h1 class="t2c-h1" style="margin:0 0 14px 0;font-family:{font};font-size:24px;line-height:1.3;font-weight:700;color:{INK};">Verify Your Email Address</h1>
      <p style="margin:0 0 24px 0;font-size:15px;line-height:1.6;color:{BODY};">We received a request to verify this email address for your {BRAND} account. Enter the code below to continue.</p>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td align="center" style="background-color:{CODE_BG};border:1px solid {CODE_BORDER};border-radius:10px;padding:20px 12px;">
          <div class="t2c-code" style="font-family:{mono};font-size:36px;line-height:1.2;font-weight:700;letter-spacing:10px;color:{INK};padding-left:10px;">{code}</div>
        </td>
      </tr></table>

      <p style="margin:18px 0 26px 0;font-size:14px;line-height:1.6;color:{BODY};text-align:center;">This code expires in <strong style="color:{INK};">{expires_minutes} minutes</strong> and can only be used once.</p>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="background-color:{NOTICE_BG};border-left:4px solid {GOLD};border-radius:6px;padding:14px 16px;font-size:13.5px;line-height:1.6;color:{BODY};">
          <strong style="color:{INK};">Never share your verification code with anyone.</strong><br>
          {BRAND} will never ask for it by email, phone or chat.
        </td>
      </tr></table>

      <p style="margin:24px 0 0 0;font-size:13.5px;line-height:1.6;color:{MUTED};">If you didn't request this code, you can safely ignore this email. No changes were made to any account.</p>
    </td></tr>

    <tr><td style="padding:22px 8px 0 8px;font-family:{font};font-size:12px;line-height:1.7;color:{MUTED};text-align:center;">
      <strong style="color:{BODY};">{BRAND}</strong> &middot; {html.escape(TAGLINE)}<br>
      This email was sent to {to}. It's an automated message, please don't reply.<br>
      &copy; {year} {BRAND}
    </td></tr>
  </table>

</td></tr>
</table>
</body>
</html>"""


def build_otp_message(sender, recipient, code, lifetime_seconds):
    """Returns a ready-to-send MIME message: multipart/related > alternative(text, html) + logo."""
    minutes = _minutes(lifetime_seconds)
    logo = _logo_bytes()

    related = MIMEMultipart("related")
    related["From"] = formataddr((BRAND, sender))
    related["To"] = recipient
    related["Subject"] = f"Your {BRAND} verification code"
    related["Date"] = formatdate(localtime=False)
    related["Message-ID"] = make_msgid(domain=(sender.split("@")[-1] or "train2conquer.local"))
    related["Auto-Submitted"] = "auto-generated"          # tells mail systems not to auto-reply
    related["X-Auto-Response-Suppress"] = "All"

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(otp_text(code, minutes), "plain", "utf-8"))     # plain text first,
    alternative.attach(MIMEText(otp_html(code, minutes, recipient, has_logo=logo is not None), "html", "utf-8"))  # best last
    related.attach(alternative)

    if logo is not None:
        image = MIMEImage(logo, _subtype="png")
        image.add_header("Content-ID", f"<{LOGO_CID}>")
        image.add_header("Content-Disposition", "inline", filename="train2conquer.png")
        related.attach(image)
    return related
