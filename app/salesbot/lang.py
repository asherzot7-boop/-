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
    "ru": {"здравствуйте", "привет", "сколько", "стоит", "хочу", "цена", "есть", "спасибо", "мне", "нужно", "нужна", "купить", "да", "нет", "добрый", "день", "можно",
           "как", "где", "когда", "что", "отправить", "документы", "паспорт", "проверка", "вы", "вас", "даёте", "гарантию", "заявка", "меня", "зовут"},
    "tr": {"merhaba", "selam", "fiyat", "fiyatı", "kaç", "kadar", "istiyorum", "var", "mı", "mi", "teşekkür", "teşekkürler", "evet", "hayır", "lütfen", "ne", "almak", "kitap", "tane", "adet", "günaydın",
           "veriyor", "musunuz", "misiniz", "nedir", "gerekli", "hangi", "nasıl", "nasil", "garanti", "belge", "belgeler", "pasaport", "sorgulama", "başvuru", "yardım"},
    "uz": {"salom", "assalomu", "alaykum", "narx", "narxi", "qancha", "kerak", "bormi", "rahmat", "olmoqchiman", "menga", "ha", "yoq", "yo'q", "yaxshi", "nechta", "kitob", "buyurtma", "qanday",
           "yubora", "oladi", "boshlig'im", "hujjat", "hujjatlar", "ismim", "kishi", "yil", "yilga", "roziman", "iltimos", "qachon"},
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
    tr, uz = len(toks & _WORDS["tr"]), len(toks & _WORDS["uz"])
    if _TR_LETTERS.search(text):
        # "Istanbul, Kadıköy. Ha, yubora oladi": a Turkish place name inside an Uzbek sentence stays Uzbek
        if uz > tr:
            return "uz", True
        return "tr", tr > 0 or uz == 0
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
    for part in re.split(r"(\{[^}]*\}|https?://\S+|@\w+|\b[A-Za-z]*[a-z][A-Z][A-Za-z]*\b|\b[A-Z]{2,}\b)", text):
        if not part or part.startswith(("{", "http", "@")) or re.fullmatch(r"[A-Za-z]*[a-z][A-Z][A-Za-z]*|[A-Z]{2,}", part):
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
        "greet": "Assalomu alaykum{name}! 😊 Men {shop} yordamchisiman. Sizni nima qiziqtiradi? Bizda:\n{catalog}",
        "greet_voice": "Assalomu alaykum! {shop}ga qo'ng'iroq qilganingiz uchun rahmat. Sizni qaysi xizmat qiziqtiradi?",
        "ask_need": "Sizni nima qiziqtiradi? Bizda:\n{catalog}\nQaysi biri kerakligini yozing — narxi va shartlarini aytaman.",
        "empty_catalog": "Hozir menejerimiz sizga mos taklifni aytadi. Telefon raqamingizni qoldiring.",
        "offer": "{product} — {price}.{desc}\nNechta kerak bo'ladi?",
        "ask_qty": "Nechta {product} kerak? Sonini yozing (masalan: 3).",
        "objection": "Tushunaman. {product} — sifatli va narxi bozorga mos. Ko'p miqdorda olsangiz, menejerimiz alohida chegirma taklif qilishi mumkin — so'rovingizni yetkazdim. Hozircha nechta kerak?",
        "ask_name": "Ajoyib! Buyurtmani rasmiylashtiramiz: ismingiz nima?",
        "ask_phone": "Rahmat, {name}! Telefon raqamingizni yozing (masalan: +998 90 123 45 67).",
        "bad_phone": "Raqam to'liq emas. Iltimos, kod bilan yozing: +998 90 123 45 67.",
        "ask_address": "Yetkazib berish manzilini yozing (shahar, ko'cha, uy). O'zingiz olib ketsangiz, «olib ketaman» deb yozing.",
        "confirm": "Buyurtmangizni tekshiring:\n• {product} × {qty} = {total}\n• {name}, {phone}\n• {address}\nHammasi to'g'rimi? «Ha» deb yozing yoki nimani o'zgartirishni ayting.",
        "fix": "Mayli, nimani o'zgartiramiz? To'g'ri ma'lumotni yozing (son, telefon yoki manzil).",
        "won": "Rahmat! Buyurtmangiz №{id} qabul qilindi ✅ Menejerimiz tez orada bog'lanib, to'lov va yetkazishni tasdiqlaydi.",
        "after_won": "Buyurtmangiz №{id} qabul qilingan, menejer bog'lanadi. Yana biror narsa kerakmi?",
        "handoff": "Albatta! So'rovingizni menejerga yetkazdim, tez orada siz bilan bog'lanadi.",
        "pickup": "O'zi olib ketadi",
        "bye_voice": "Rahmat, xayr! Yaxshi kun tilaymiz.",
        "noinput_voice": "Kechirasiz, eshitmadim. Iltimos, qaytadan ayting.",
        "phone_button": "📞 Raqamni yuborish",
        "offer_service": "{product} — {price}.{desc}\nNecha kishi uchun?",
        "ask_name_service": "Ajoyib! Arizani rasmiylashtiramiz: ismingiz nima?",
        "ask_qty_service": "Necha kishi uchun {product}? Sonini yozing (masalan: 2).",
        "ask_details": "Rejangizni qisqacha yozing: qachon va qaysi shaharga (masalan: dekabr, Istanbul).",
        "confirm_service": "Arizangizni tekshiring:\n• {product}, {qty} kishi — {total}\n• {name}, {phone}\n• {address}\nHammasi to'g'rimi? «Ha» deb yozing yoki nimani o'zgartirishni ayting.",
        "won_service": "Rahmat! Arizangiz №{id} qabul qilindi ✅ Konsultantimiz tez orada bog'lanib, hujjatlar ro'yxati va keyingi qadamlarni aytadi.",
        "docs": "{product} uchun odatda kerak bo'ladigan hujjatlar:\n{docs}\nAniq ro'yxatni konsultantimiz holatingizga qarab tasdiqlaydi.",
        "docs_none": "Hujjatlar ro'yxati holatingizga bog'liq — konsultantimiz aniq aytib beradi.",
        "price_note": "Yakuniy narxni konsultant holatingizga qarab aytadi.",
        "prepay": "Oldindan to'lov: {amount}.",
        "prepay_short": "oldindan to'lov {amount}",
        "result_time": "Natija: {time}.",
        "consent": "Hujjatlaringizni faqat arizangizni rasmiylashtirish uchun shifrlangan holda saqlaymiz va xizmat tugagach {days} kundan keyin o'chiramiz. Rozimisiz? «Roziman» deb yozing, keyin hujjatni qayta yuboring.",
        "consent_ok": "Rahmat! Endi hujjatni qayta yuboring.",
        "consent_pre": "Hujjatlarni yuborishdan oldin: ularni faqat arizangiz uchun shifrlangan holda saqlaymiz va xizmat tugagach {days} kundan keyin o'chiramiz. Rozimisiz? «Roziman» deb yozing.",
        "consent_thanks": "Rahmat! Endi hujjatlarni shu yerga yuboring (PDF yoki rasm).",
        "docs_state": "Qabul qilingan: {got}\nQoldi: {left}",
        "docs_done": "Barcha hujjatlar qabul qilindi ✅",
        "docs_reminder": "Assalomu alaykum{name}! Arizangiz №{id} uchun hujjatlar yetishmayapti: {left}. PDF yoki rasm qilib shu yerga yuboring.",
        "status_names": "Hujjatlar kutilmoqda|Hujjatlar qabul qilindi|Ariza topshirildi|Natija kutilmoqda|Yakunlandi ✅",
        "status_msg": "Arizangiz №{id} holati: {status}.{note}",
        "review_ask": "Xizmatimizni 1 dan 5 gacha baholang (raqam yozing). Fikringiz biz uchun muhim!",
        "review_good": "Rahmat! 🙏 Do'stlaringizga tavsiya qiling: ular «{code}» kodini yozsa, menejerimiz alohida taklif beradi.",
        "review_bad": "Uzr so'raymiz. Rahbariyatimiz siz bilan bog'lanib, nima bo'lganini so'raydi.",
        "file_received": "Hujjat qabul qilindi ✅ (jami {n} ta). Konsultantimiz ko'rib chiqadi.",
        "file_failed": "Faylni ololmadim. Iltimos, PDF yoki rasm ko'rinishida qayta yuboring.",
        "file_too_big": "Fayl juda katta ({mb} MB dan oshmasin). Iltimos, kichikroq qilib yuboring.",
        "voice_failed": "Ovozli xabarni tushuna olmadim. Iltimos, yozib yuboring — xabaringizni menejerga ham yetkazdim.",
        "pay_link": "To'lov: {amount}.\nTo'lash uchun havola: {link}\nTo'lovdan keyin konsultantimiz ishni boshlaydi.",
        "pay_card": "Oldindan to'lov: {amount}.\nKarta: {card}\nIzohda ariza raqamini yozing: №{id}. To'lov chekini shu yerga yuboring.",
        "paid": "To'lovingiz qabul qilindi ✅ Ishni boshladik, natija haqida shu yerda xabar beramiz.",
        "reminder": "Assalomu alaykum{name}! Arizangizni tugatamizmi? {question}",
        "from_manager": "👤 Menejer: {text}",
        "discount_note": "Chegirma bo'yicha qarorni menejerimiz qabul qiladi — so'rovingizni yetkazdim.",
        "passport_ok": "Pasport o'qildi: {name}, amal qilish muddati {date}.",
        "passport_expiring": "Diqqat: pasportingiz muddati {date} da tugaydi (6 oydan kam qoldi). Konsultantimiz bu holatni alohida ko'rib chiqadi.",
        "slots": "Konsultatsiya uchun qulay vaqtni tanlang (raqamini yozing):\n{slots}\nBoshqa vaqt kerak bo'lsa, o'zingiz yozing.",
        "booking_reminder": "Eslatma: konsultatsiyangiz bugun soat {time} da. Kutib qolamiz!",
        "weekdays": "Dushanba|Seshanba|Chorshanba|Payshanba|Juma|Shanba|Yakshanba",
        "months": "yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sentabr|oktabr|noyabr|dekabr",
        "reengage": "Assalomu alaykum{name}! Turkiya bo'yicha savolingiz hal bo'ldimi{service}? Yordam kerak bo'lsa, shu yerga yozing — javob beramiz.",
        "result_ban": "Pasportingiz tekshirildi: Turkiyaga kirishda taqiq (deport) bor.{note}",
        "result_ok": "Pasportingiz tekshirildi: Turkiyaga kirishda taqiq yo'q ✅{note}",
        "upsell": "Bizda «{product}» xizmati bor — {price}.{extras}\nRasmiylashtiramizmi? «Ha» deb yozing.",
        "no_thanks": "Mayli! Kerak bo'lsa, shu yerga yozing — yordam beramiz.",
        "faq_unknown": "Bu savolga konsultantimiz aniq javob beradi — so'rovingizni yetkazdim, tez orada shu yerga yozadi.",
        "comment_public": "Javobni Direct'ga yubordik ✅|Batafsil Direct'da ✅|Direct'ingizni tekshiring, yozdik ✅",
        "comment_public_nodm": "Iltimos, Direct'ga yozing — narx va shartlarni aytamiz ✅",
    },
    "tr": {
        "greet": "Merhaba{name}! 😊 Ben {shop} asistanıyım. Size nasıl yardımcı olabilirim? Hizmetlerimiz:\n{catalog}",
        "greet_voice": "Merhaba! {shop}'i aradığınız için teşekkürler. Hangi hizmetle ilgileniyorsunuz?",
        "ask_need": "Neyle ilgileniyorsunuz? Hizmetlerimiz:\n{catalog}\nHangisini istediğinizi yazın, fiyat ve koşulları söyleyeyim.",
        "empty_catalog": "Yöneticimiz size uygun teklifi iletecek. Lütfen telefon numaranızı bırakın.",
        "offer": "{product} — {price}.{desc}\nKaç adet istersiniz?",
        "ask_qty": "Kaç adet {product} istersiniz? Lütfen sayı yazın (ör. 3).",
        "objection": "Anlıyorum. {product} kaliteli ve fiyatı piyasaya uygun. Çok adet alırsanız yöneticimiz özel indirim sunabilir — talebinizi ilettim. Şimdilik kaç adet istersiniz?",
        "ask_name": "Harika! Siparişinizi oluşturalım: adınız nedir?",
        "ask_phone": "Teşekkürler, {name}! Telefon numaranızı yazar mısınız? (ör. +90 532 123 45 67)",
        "bad_phone": "Numara eksik görünüyor. Lütfen ülke koduyla yazın: +90 532 123 45 67.",
        "ask_address": "Teslimat adresini yazın (şehir, sokak, numara). Kendiniz alacaksanız «gelip alırım» yazın.",
        "confirm": "Siparişinizi kontrol edin:\n• {product} × {qty} = {total}\n• {name}, {phone}\n• {address}\nHer şey doğru mu? «Evet» yazın ya da neyi değiştireceğimizi söyleyin.",
        "fix": "Tamam, neyi değiştirelim? Doğru bilgiyi yazın (adet, telefon veya adres).",
        "won": "Teşekkürler! №{id} numaralı siparişiniz alındı ✅ Yöneticimiz kısa süre içinde ödeme ve teslimatı onaylamak için sizi arayacak.",
        "after_won": "№{id} numaralı siparişiniz alındı, yöneticimiz sizi arayacak. Başka bir şey ister misiniz?",
        "handoff": "Elbette! Talebinizi yöneticimize ilettim, kısa süre içinde sizinle iletişime geçecek.",
        "pickup": "Kendisi alacak",
        "bye_voice": "Teşekkürler, iyi günler!",
        "noinput_voice": "Özür dilerim, duyamadım. Lütfen tekrar söyler misiniz?",
        "phone_button": "📞 Numaramı gönder",
        "offer_service": "{product} — {price}.{desc}\nKaç kişi için?",
        "ask_name_service": "Harika! Başvurunuzu oluşturalım: adınız nedir?",
        "ask_qty_service": "{product} kaç kişi için? Lütfen sayı yazın (ör. 2).",
        "ask_details": "Planınızı kısaca yazın: ne zaman ve hangi şehre (ör. aralık, Taşkent).",
        "confirm_service": "Başvurunuzu kontrol edin:\n• {product}, {qty} kişi — {total}\n• {name}, {phone}\n• {address}\nHer şey doğru mu? «Evet» yazın ya da neyi değiştireceğimizi söyleyin.",
        "won_service": "Teşekkürler! №{id} numaralı başvurunuz alındı ✅ Danışmanımız kısa süre içinde sizi arayıp gerekli belgeleri ve sonraki adımları anlatacak.",
        "docs": "{product} için genellikle gereken belgeler:\n{docs}\nKesin listeyi danışmanımız durumunuza göre onaylar.",
        "docs_none": "Belge listesi durumunuza göre değişir — danışmanımız size net bilgi verecek.",
        "price_note": "Kesin fiyatı danışmanımız durumunuza göre belirler.",
        "prepay": "Ön ödeme: {amount}.",
        "prepay_short": "ön ödeme {amount}",
        "result_time": "Sonuç: {time}.",
        "consent": "Belgelerinizi yalnızca başvurunuz için şifreli olarak saklıyor ve hizmet bittikten {days} gün sonra siliyoruz. Onaylıyor musunuz? «Onaylıyorum» yazın, sonra belgeyi tekrar gönderin.",
        "consent_ok": "Teşekkürler! Şimdi belgeyi tekrar gönderin.",
        "consent_pre": "Belgeleri göndermeden önce: yalnızca başvurunuz için şifreli olarak saklıyor ve hizmet bittikten {days} gün sonra siliyoruz. Onaylıyor musunuz? «Onaylıyorum» yazın.",
        "consent_thanks": "Teşekkürler! Şimdi belgeleri buraya gönderin (PDF veya fotoğraf).",
        "docs_state": "Alınan: {got}\nKalan: {left}",
        "docs_done": "Tüm belgeler alındı ✅",
        "docs_reminder": "Merhaba{name}! №{id} numaralı başvurunuz için eksik belgeler: {left}. PDF veya fotoğraf olarak buraya gönderin.",
        "status_names": "Belgeler bekleniyor|Belgeler alındı|Başvuru yapıldı|Sonuç bekleniyor|Tamamlandı ✅",
        "status_msg": "№{id} numaralı başvurunuzun durumu: {status}.{note}",
        "review_ask": "Hizmetimizi 1'den 5'e kadar puanlayın (rakam yazın). Görüşünüz bizim için önemli!",
        "review_good": "Teşekkürler! 🙏 Arkadaşlarınıza önerin: «{code}» kodunu yazarlarsa yöneticimiz özel teklif sunar.",
        "review_bad": "Özür dileriz. Yönetimimiz sizinle iletişime geçip ne olduğunu soracak.",
        "file_received": "Belge alındı ✅ (toplam {n}). Danışmanımız inceleyecek.",
        "file_failed": "Dosyayı alamadım. Lütfen PDF veya fotoğraf olarak tekrar gönderin.",
        "file_too_big": "Dosya çok büyük ({mb} MB'ı geçmemeli). Lütfen daha küçük gönderin.",
        "voice_failed": "Sesli mesajı anlayamadım. Lütfen yazarak gönderin — mesajınızı yöneticimize de ilettim.",
        "pay_link": "Ödeme: {amount}.\nÖdeme bağlantısı: {link}\nÖdemeden sonra danışmanımız işleme başlar.",
        "pay_card": "Ön ödeme: {amount}.\nKart: {card}\nAçıklamaya başvuru numarasını yazın: №{id}. Dekontu buraya gönderin.",
        "paid": "Ödemeniz alındı ✅ İşleme başladık, sonucu buradan bildireceğiz.",
        "reminder": "Merhaba{name}! Başvurunuzu tamamlayalım mı? {question}",
        "from_manager": "👤 Yönetici: {text}",
        "discount_note": "İndirim kararını yöneticimiz verir — talebinizi ilettim.",
        "passport_ok": "Pasaport okundu: {name}, geçerlilik {date}.",
        "passport_expiring": "Dikkat: pasaportunuzun süresi {date} tarihinde doluyor (6 aydan az kaldı). Danışmanımız bu durumu ayrıca inceleyecek.",
        "slots": "Görüşme için uygun saati seçin (numarasını yazın):\n{slots}\nBaşka bir saat isterseniz yazın.",
        "booking_reminder": "Hatırlatma: görüşmeniz bugün saat {time}'de. Bekliyoruz!",
        "weekdays": "Pazartesi|Salı|Çarşamba|Perşembe|Cuma|Cumartesi|Pazar",
        "months": "Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık",
        "reengage": "Merhaba{name}! Sorunuz çözüldü mü{service}? Yardım gerekirse buraya yazın, yanıtlayalım.",
        "result_ban": "Pasaportunuz sorgulandı: Türkiye'ye giriş yasağı (deport) var.{note}",
        "result_ok": "Pasaportunuz sorgulandı: Türkiye'ye giriş yasağı yok ✅{note}",
        "upsell": "«{product}» hizmetimiz var — {price}.{extras}\nBaşvuru yapalım mı? «Evet» yazın.",
        "no_thanks": "Tamam! İhtiyacınız olursa buraya yazın, yardımcı oluruz.",
        "faq_unknown": "Bu soruyu danışmanımız net olarak yanıtlayacak — talebinizi ilettim, kısa süre içinde buradan yazacak.",
        "comment_public": "Cevabı DM'den gönderdik ✅|Detaylar DM'de ✅|DM kutunuzu kontrol edin ✅",
        "comment_public_nodm": "Lütfen DM'den yazın — fiyat ve koşulları iletelim ✅",
    },
    "ru": {
        "greet": "Здравствуйте{name}! 😊 Я помощник {shop}. Что вас интересует? Мы предлагаем:\n{catalog}",
        "greet_voice": "Здравствуйте! Спасибо, что позвонили в {shop}. Какая услуга вас интересует?",
        "ask_need": "Что вас интересует? Мы предлагаем:\n{catalog}\nНапишите, что нужно — скажу цену и условия.",
        "empty_catalog": "Наш менеджер подберёт для вас предложение. Оставьте, пожалуйста, номер телефона.",
        "offer": "{product} — {price}.{desc}\nСколько штук вам нужно?",
        "ask_qty": "Сколько штук {product} вам нужно? Напишите число (например: 3).",
        "objection": "Понимаю. {product} — качественный товар по рыночной цене. При большом объёме менеджер может предложить скидку — я передал ваш запрос. Сколько штук вам нужно сейчас?",
        "ask_name": "Отлично! Оформим заказ: как вас зовут?",
        "ask_phone": "Спасибо, {name}! Напишите номер телефона (например: +998 90 123 45 67).",
        "bad_phone": "Номер неполный. Напишите с кодом: +998 90 123 45 67.",
        "ask_address": "Напишите адрес доставки (город, улица, дом). Если заберёте сами — напишите «самовывоз».",
        "confirm": "Проверьте заказ:\n• {product} × {qty} = {total}\n• {name}, {phone}\n• {address}\nВсё верно? Напишите «Да» или что нужно изменить.",
        "fix": "Хорошо, что изменить? Напишите правильные данные (количество, телефон или адрес).",
        "won": "Спасибо! Заказ №{id} принят ✅ Менеджер скоро свяжется с вами для подтверждения оплаты и доставки.",
        "after_won": "Ваш заказ №{id} принят, менеджер свяжется с вами. Нужно что-нибудь ещё?",
        "handoff": "Конечно! Я передал ваш запрос менеджеру, он скоро свяжется с вами.",
        "pickup": "Самовывоз",
        "bye_voice": "Спасибо, до свидания!",
        "noinput_voice": "Извините, не расслышал. Повторите, пожалуйста.",
        "phone_button": "📞 Отправить номер",
        "offer_service": "{product} — {price}.{desc}\nНа сколько человек?",
        "ask_name_service": "Отлично! Оформим заявку: как вас зовут?",
        "ask_qty_service": "На сколько человек {product}? Напишите число (например: 2).",
        "ask_details": "Коротко опишите план: когда и в какой город (например: декабрь, Стамбул).",
        "confirm_service": "Проверьте заявку:\n• {product}, {qty} чел. — {total}\n• {name}, {phone}\n• {address}\nВсё верно? Напишите «Да» или что нужно изменить.",
        "won_service": "Спасибо! Заявка №{id} принята ✅ Консультант скоро свяжется с вами и расскажет, какие документы нужны и что дальше.",
        "docs": "Для услуги «{product}» обычно нужны документы:\n{docs}\nТочный список консультант подтвердит под вашу ситуацию.",
        "docs_none": "Список документов зависит от ситуации — консультант скажет точно.",
        "price_note": "Окончательную цену консультант назовёт после оценки вашей ситуации.",
        "prepay": "Предоплата: {amount}.",
        "prepay_short": "предоплата {amount}",
        "result_time": "Результат: {time}.",
        "consent": "Мы храним ваши документы в зашифрованном виде только для оформления заявки и удаляем их через {days} дней после окончания услуги. Вы согласны? Напишите «Согласен», затем отправьте документ ещё раз.",
        "consent_ok": "Спасибо! Теперь отправьте документ ещё раз.",
        "consent_pre": "Перед отправкой документов: мы храним их в зашифрованном виде только для вашей заявки и удаляем через {days} дней после окончания услуги. Вы согласны? Напишите «Согласен».",
        "consent_thanks": "Спасибо! Теперь отправьте документы сюда (PDF или фото).",
        "docs_state": "Получено: {got}\nОсталось: {left}",
        "docs_done": "Все документы получены ✅",
        "docs_reminder": "Здравствуйте{name}! Для заявки №{id} не хватает документов: {left}. Пришлите их сюда в PDF или фото.",
        "status_names": "Ожидаем документы|Документы получены|Заявка подана|Ожидаем результат|Завершено ✅",
        "status_msg": "Статус заявки №{id}: {status}.{note}",
        "review_ask": "Оцените нашу работу от 1 до 5 (напишите цифру). Ваше мнение важно для нас!",
        "review_good": "Спасибо! 🙏 Порекомендуйте нас друзьям: если они напишут код «{code}», менеджер предложит особые условия.",
        "review_bad": "Приносим извинения. Руководство свяжется с вами и узнает, что пошло не так.",
        "file_received": "Документ получен ✅ (всего {n}). Консультант его посмотрит.",
        "file_failed": "Не удалось получить файл. Пришлите, пожалуйста, ещё раз в виде PDF или фото.",
        "file_too_big": "Файл слишком большой (не больше {mb} МБ). Пришлите, пожалуйста, поменьше.",
        "voice_failed": "Не удалось разобрать голосовое сообщение. Напишите, пожалуйста, текстом — я передал его и менеджеру.",
        "pay_link": "Оплата: {amount}.\nСсылка для оплаты: {link}\nПосле оплаты консультант начнёт работу.",
        "pay_card": "Предоплата: {amount}.\nКарта: {card}\nВ комментарии укажите номер заявки: №{id}. Чек пришлите сюда.",
        "paid": "Оплата получена ✅ Мы начали работу и сообщим результат здесь.",
        "reminder": "Здравствуйте{name}! Завершим вашу заявку? {question}",
        "from_manager": "👤 Менеджер: {text}",
        "discount_note": "Решение о скидке принимает менеджер — я передал ваш запрос.",
        "passport_ok": "Паспорт распознан: {name}, действителен до {date}.",
        "passport_expiring": "Внимание: срок действия паспорта истекает {date} (осталось меньше 6 месяцев). Консультант рассмотрит это отдельно.",
        "slots": "Выберите удобное время консультации (напишите номер):\n{slots}\nЕсли нужно другое время, напишите его.",
        "booking_reminder": "Напоминание: ваша консультация сегодня в {time}. Ждём вас!",
        "weekdays": "Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье",
        "months": "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря",
        "reengage": "Здравствуйте{name}! Удалось решить вопрос{service}? Если нужна помощь, напишите сюда — ответим.",
        "result_ban": "Ваш паспорт проверен: есть запрет на въезд в Турцию (депорт).{note}",
        "result_ok": "Ваш паспорт проверен: запрета на въезд в Турцию нет ✅{note}",
        "upsell": "У нас есть услуга «{product}» — {price}.{extras}\nОформим заявку? Напишите «Да».",
        "no_thanks": "Хорошо! Если понадобится, напишите сюда — поможем.",
        "faq_unknown": "На этот вопрос точно ответит консультант — я передал ваш запрос, он скоро напишет сюда.",
        "comment_public": "Ответили вам в Direct ✅|Подробности в Direct ✅|Проверьте Direct, мы написали ✅",
        "comment_public_nodm": "Напишите, пожалуйста, в Direct — расскажем цену и условия ✅",
    },
}


def text(lang: str, key: str, **kw) -> str:
    base = "uz" if lang == "uzc" else (lang if lang in TEXTS else "uz")
    template = TEXTS[base][key]
    if lang == "uzc":
        template = to_cyrillic(template)
        kw = {k: (to_cyrillic(v) if k in _TRANSLIT_KEYS and isinstance(v, str) else v) for k, v in kw.items()}
    return template.format(**kw)


_TRANSLIT_KEYS = {"product", "desc", "catalog", "docs"}


# ----------------------------------------------------------------- vocabularies for intent detection
STATUS_Q = re.compile(r"holat|holati|qayerda|nima bo'ldi|arizam|durum|ne oldu|başvurum|basvurum|статус|где моя|что с заявкой|ҳолат|қаерда|аризам", re.IGNORECASE)
REFERRAL = re.compile(r"\bNT[- ]?(\d{1,6})\b", re.IGNORECASE)
CONSENT = re.compile(r"roziman|rozimiz|розиман|onaylıyorum|onayliyorum|kabul ediyorum|согласен|согласна|согласны", re.IGNORECASE)
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
DOCS = re.compile(r"hujjat|xujjat|документ|dokument|belge|evrak|nima\s+kerak\s+bo'ladi|nimalar\s+kerak|ne\s+gerekli|neler\s+gerekli|что\s+нужно", re.IGNORECASE)
PRICE = re.compile(r"narx|qancha|necha\s+pul|fiyat|ne\s+kadar|kaç\s+para|tsena|skolko|stoit|price", re.IGNORECASE)
