"""Language detection, Uzbek Latin <-> Cyrillic transliteration and reply texts.

Languages: "uz" (Uzbek Latin), "uzc" (Uzbek Cyrillic), "tr" (Turkish), "ru" (Russian).
Uzbek Cyrillic texts are produced from the Latin ones by transliteration, so
every phrase is written once.
"""
from __future__ import annotations

import re

LANGS = ("uz", "uzc", "tr", "ru")

_CYR = re.compile(r"[а-яёўқғҳ]", re.IGNORECASE)
_UZ_CYR_LETTERS = re.compile(r"[ўқғҳ]", re.IGNORECASE)
_TR_LETTERS = re.compile(r"[çşğıöüİ]")
_APOS = "'ʻʼ‘’`"

_WORDS = {
    "uzc": {"салом", "ассалому", "алайкум", "нарх", "нархи", "қанча", "керак", "борми", "бор", "рахмат", "раҳмат", "китоб", "олмоқчиман", "менга", "ҳа", "йўқ", "яхши", "неча"},
    "ru": {"здравствуйте", "привет", "сколько", "стоит", "хочу", "цена", "есть", "спасибо", "мне", "нужно", "нужна", "купить", "да", "нет", "добрый", "день", "можно"},
    "tr": {"merhaba", "selam", "fiyat", "fiyatı", "kaç", "kadar", "istiyorum", "var", "mı", "mi", "teşekkür", "teşekkürler", "evet", "hayır", "lütfen", "ne", "almak", "kitap", "tane", "adet", "günaydın"},
    "uz": {"salom", "assalomu", "alaykum", "narx", "narxi", "qancha", "kerak", "bormi", "rahmat", "olmoqchiman", "menga", "ha", "yoq", "yo'q", "yaxshi", "nechta", "kitob", "buyurtma", "qanday"},
}


def _tokens(text: str) -> list[str]:
    t = text.lower()
    for a in _APOS:
        t = t.replace(a, "'")
    return re.findall(r"[\w']+", t)


def detect(text: str, default: str = "uz") -> tuple[str, bool]:
    """Return (language, confident). Not confident -> keep the lead's language."""
    if not text or not text.strip():
        return default, False
    toks = set(_tokens(text))
    if _CYR.search(text):
        if _UZ_CYR_LETTERS.search(text):
            return "uzc", True
        uzc, ru = len(toks & _WORDS["uzc"]), len(toks & _WORDS["ru"])
        if uzc > ru:
            return "uzc", True
        if ru > uzc:
            return "ru", True
        return ("uzc" if default in ("uz", "uzc") else "ru"), False
    if _TR_LETTERS.search(text):
        return "tr", True
    tr, uz = len(toks & _WORDS["tr"]), len(toks & _WORDS["uz"])
    if any(w in text.lower() for w in ("o'", "g'", "oʻ", "gʻ")) or uz > tr:
        return "uz", uz > 0 or "'" in text
    if tr > uz:
        return "tr", True
    return default, False


# ----------------------------------------------------------------- transliteration
_LAT2CYR_MULTI = [
    ("o'", "ў"), ("g'", "ғ"), ("sh", "ш"), ("ch", "ч"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ye", "е"),
]
_LAT2CYR = dict(zip("abdefghijklmnopqrstuvxyz", "абдефгҳижклмнопқрстувхйз"))
_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}


def to_cyrillic(text: str) -> str:
    """Uzbek Latin -> Cyrillic. Text inside {braces} or URLs is left as is."""
    out: list[str] = []
    for part in re.split(r"(\{[^}]*\}|https?://\S+|@\w+)", text):
        if not part or part.startswith(("{", "http", "@")):
            out.append(part)
            continue
        out.append(_word_translit(part))
    return "".join(out)


def _word_translit(s: str) -> str:
    for a in _APOS:
        s = s.replace(a, "'")
    res: list[str] = []
    i = 0
    prev_letter = False
    while i < len(s):
        ch = s[i]
        low = s[i:i + 2].lower()
        upper = ch.isupper()
        rep = None
        for lat, cyr in _LAT2CYR_MULTI:
            if low == lat:
                rep, i = cyr, i + 2
                break
        if rep is None:
            c = ch.lower()
            if c == "e" and not prev_letter:
                rep = "э"
            elif c in _LAT2CYR:
                rep = _LAT2CYR[c]
            elif c == "'":
                rep = "ъ" if prev_letter else ""
            else:
                rep = ch
            i += 1
        if upper:
            rep = rep[:1].upper() + rep[1:]
        res.append(rep)
        prev_letter = ch.isalpha()
    return "".join(res)


def to_latin(text: str) -> str:
    """Cyrillic (Uzbek or Russian) -> Uzbek Latin, lower-case. Used only for matching."""
    return "".join(_CYR2LAT.get(c, c) for c in text.lower())


def normalize(text: str) -> str:
    t = to_latin(text)
    for a in _APOS:
        t = t.replace(a, "'")
    return t


# ----------------------------------------------------------------- reply texts
TEXTS: dict[str, dict[str, str]] = {
    "uz": {
        "greet": "Assalomu alaykum{name}! 😊 {shop} savdo bo'limiman. Sizga nima kerak? Bizda:\n{catalog}",
        "greet_voice": "Assalomu alaykum! {shop}ga qo'ng'iroq qilganingiz uchun rahmat. Qaysi mahsulot sizni qiziqtiradi?",
        "ask_need": "Qaysi mahsulot qiziqtiradi? Bizda:\n{catalog}\nNomini yozing — narxi va shartlarini aytaman.",
        "empty_catalog": "Hozir menejerimiz sizga mos taklifni aytadi. Telefon raqamingizni qoldiring.",
        "offer": "{product} — {price} so'm.{desc}\nNechta kerak bo'ladi?",
        "ask_qty": "Nechta {product} kerak? Sonini yozing (masalan: 3).",
        "objection": "Tushunaman. {product} — sifatli va narxi bozorga mos. Ko'p miqdorda olsangiz, menejerimiz alohida chegirma taklif qilishi mumkin — so'rovingizni yetkazdim. Hozircha nechta kerak?",
        "ask_name": "Ajoyib! Buyurtmani rasmiylashtiramiz: ismingiz nima?",
        "ask_phone": "Rahmat, {name}! Telefon raqamingizni yozing (masalan: +998 90 123 45 67).",
        "bad_phone": "Raqam to'liq emas. Iltimos, kod bilan yozing: +998 90 123 45 67.",
        "ask_address": "Yetkazib berish manzilini yozing (shahar, ko'cha, uy). O'zingiz olib ketsangiz, «olib ketaman» deb yozing.",
        "confirm": "Buyurtmangizni tekshiring:\n• {product} × {qty} = {total} so'm\n• {name}, {phone}\n• {address}\nHammasi to'g'rimi? «Ha» deb yozing yoki nimani o'zgartirishni ayting.",
        "fix": "Mayli, nimani o'zgartiramiz? To'g'ri ma'lumotni yozing (son, telefon yoki manzil).",
        "won": "Rahmat! Buyurtmangiz №{id} qabul qilindi ✅ Menejerimiz tez orada bog'lanib, to'lov va yetkazishni tasdiqlaydi.",
        "after_won": "Buyurtmangiz №{id} qabul qilingan, menejer bog'lanadi. Yana biror narsa kerakmi?",
        "handoff": "Albatta! So'rovingizni menejerga yetkazdim, tez orada siz bilan bog'lanadi.",
        "pickup": "O'zi olib ketadi",
        "bye_voice": "Rahmat, xayr! Yaxshi kun tilaymiz.",
        "noinput_voice": "Kechirasiz, eshitmadim. Iltimos, qaytadan ayting.",
        "phone_button": "📞 Raqamni yuborish",
    },
    "tr": {
        "greet": "Merhaba{name}! 😊 {shop} satış ekibiyim. Size nasıl yardımcı olabilirim? Ürünlerimiz:\n{catalog}",
        "greet_voice": "Merhaba! {shop}'i aradığınız için teşekkürler. Hangi ürünle ilgileniyorsunuz?",
        "ask_need": "Hangi ürünle ilgileniyorsunuz? Ürünlerimiz:\n{catalog}\nÜrün adını yazın, fiyat ve koşulları söyleyeyim.",
        "empty_catalog": "Yöneticimiz size uygun teklifi iletecek. Lütfen telefon numaranızı bırakın.",
        "offer": "{product} — {price} so'm.{desc}\nKaç adet istersiniz?",
        "ask_qty": "Kaç adet {product} istersiniz? Lütfen sayı yazın (ör. 3).",
        "objection": "Anlıyorum. {product} kaliteli ve fiyatı piyasaya uygun. Çok adet alırsanız yöneticimiz özel indirim sunabilir — talebinizi ilettim. Şimdilik kaç adet istersiniz?",
        "ask_name": "Harika! Siparişinizi oluşturalım: adınız nedir?",
        "ask_phone": "Teşekkürler, {name}! Telefon numaranızı yazar mısınız? (ör. +90 532 123 45 67)",
        "bad_phone": "Numara eksik görünüyor. Lütfen ülke koduyla yazın: +90 532 123 45 67.",
        "ask_address": "Teslimat adresini yazın (şehir, sokak, numara). Kendiniz alacaksanız «gelip alırım» yazın.",
        "confirm": "Siparişinizi kontrol edin:\n• {product} × {qty} = {total} so'm\n• {name}, {phone}\n• {address}\nHer şey doğru mu? «Evet» yazın ya da neyi değiştireceğimizi söyleyin.",
        "fix": "Tamam, neyi değiştirelim? Doğru bilgiyi yazın (adet, telefon veya adres).",
        "won": "Teşekkürler! №{id} numaralı siparişiniz alındı ✅ Yöneticimiz kısa süre içinde ödeme ve teslimatı onaylamak için sizi arayacak.",
        "after_won": "№{id} numaralı siparişiniz alındı, yöneticimiz sizi arayacak. Başka bir şey ister misiniz?",
        "handoff": "Elbette! Talebinizi yöneticimize ilettim, kısa süre içinde sizinle iletişime geçecek.",
        "pickup": "Kendisi alacak",
        "bye_voice": "Teşekkürler, iyi günler!",
        "noinput_voice": "Özür dilerim, duyamadım. Lütfen tekrar söyler misiniz?",
        "phone_button": "📞 Numaramı gönder",
    },
    "ru": {
        "greet": "Здравствуйте{name}! 😊 Это отдел продаж {shop}. Что вас интересует? У нас есть:\n{catalog}",
        "greet_voice": "Здравствуйте! Спасибо, что позвонили в {shop}. Какой товар вас интересует?",
        "ask_need": "Какой товар вас интересует? У нас есть:\n{catalog}\nНапишите название — скажу цену и условия.",
        "empty_catalog": "Наш менеджер подберёт для вас предложение. Оставьте, пожалуйста, номер телефона.",
        "offer": "{product} — {price} сум.{desc}\nСколько штук вам нужно?",
        "ask_qty": "Сколько штук {product} вам нужно? Напишите число (например: 3).",
        "objection": "Понимаю. {product} — качественный товар по рыночной цене. При большом объёме менеджер может предложить скидку — я передал ваш запрос. Сколько штук вам нужно сейчас?",
        "ask_name": "Отлично! Оформим заказ: как вас зовут?",
        "ask_phone": "Спасибо, {name}! Напишите номер телефона (например: +998 90 123 45 67).",
        "bad_phone": "Номер неполный. Напишите с кодом: +998 90 123 45 67.",
        "ask_address": "Напишите адрес доставки (город, улица, дом). Если заберёте сами — напишите «самовывоз».",
        "confirm": "Проверьте заказ:\n• {product} × {qty} = {total} сум\n• {name}, {phone}\n• {address}\nВсё верно? Напишите «Да» или что нужно изменить.",
        "fix": "Хорошо, что изменить? Напишите правильные данные (количество, телефон или адрес).",
        "won": "Спасибо! Заказ №{id} принят ✅ Менеджер скоро свяжется с вами для подтверждения оплаты и доставки.",
        "after_won": "Ваш заказ №{id} принят, менеджер свяжется с вами. Нужно что-нибудь ещё?",
        "handoff": "Конечно! Я передал ваш запрос менеджеру, он скоро свяжется с вами.",
        "pickup": "Самовывоз",
        "bye_voice": "Спасибо, до свидания!",
        "noinput_voice": "Извините, не расслышал. Повторите, пожалуйста.",
        "phone_button": "📞 Отправить номер",
    },
}


def text(lang: str, key: str, **kw) -> str:
    base = "uz" if lang == "uzc" else (lang if lang in TEXTS else "uz")
    template = TEXTS[base][key]
    if lang == "uzc":
        template = to_cyrillic(template)
        kw = {k: (to_cyrillic(v) if k in _TRANSLIT_KEYS and isinstance(v, str) else v) for k, v in kw.items()}
    return template.format(**kw)


_TRANSLIT_KEYS = {"product", "desc", "catalog"}


# ----------------------------------------------------------------- vocabularies for intent detection
YES = {"ha", "xa", "ha'", "albatta", "ok", "okay", "ok'", "bo'ladi", "boladi", "to'g'ri", "togri", "roziman", "mayli", "tasdiqlayman",
       "evet", "tamam", "olur", "doğru", "dogru", "onaylıyorum", "da", "da'", "verno", "xorosho", "podtverjdayu", "aga", "+"}
NO = {"yo'q", "yoq", "yo'q.", "hayır", "hayir", "net", "noto'g'ri", "notogri", "yanlış", "neverno"}
OPERATOR = re.compile(
    r"operator|menejer|menedjer|manager|jonli\s+odam|odam\s+bilan|yetkili|temsilci|insanla|operatorga|"
    r"chelovek|s\s+chelovekom|позвоните|qo'ng'iroq\s+qiling|perezvonite|beni\s+arayın|arayın",
    re.IGNORECASE,
)
DISCOUNT = re.compile(r"chegirma|skidka|indirim|arzonroq|arzonlashtir|ucuz|kampanya|aksiya", re.IGNORECASE)
EXPENSIVE = re.compile(r"qimmat|pahal|dorogo|dorogovato|ko'p\s+pul", re.IGNORECASE)
PICKUP = re.compile(r"olib\s+ketaman|o'zim\s+olaman|o'zim\s+kelaman|gelip\s+al|kendim\s+al|samovyvoz|sam\s+zaber|zaberu", re.IGNORECASE)
BUY = re.compile(r"olaman|olmoqchi|sotib|buyurtma|zakaz|kerak|almak|istiyorum|sipariş|siparis|alayım|kupit|xochu|nujno|nujna|beraman", re.IGNORECASE)
PRICE = re.compile(r"narx|qancha|necha\s+pul|fiyat|ne\s+kadar|kaç\s+para|tsena|skolko|stoit|price", re.IGNORECASE)
