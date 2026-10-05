"""Aşama 4: Kişiselleştirilmiş cevaplar + kalıcı sohbet ve hafıza (Streamlit arayüzü).

Çalıştırma (proje KÖK klasöründen):
    streamlit run streamlit_app.py

Bu dosyanın sorumluluğu arayüz ve akışın yönetimi:
- eldeki mesajları çizmek, kullanıcıdan yeni mesaj almak,
- system prompt'u app/prompt_builder.py'den, cevabı app/llm_client.py'den istemek,
- soru-cevabı app/database.py ile kaydetmek,
- kalıcı tercihleri app/memory.py ile yakalamak.
LLM API'sine doğrudan erişim YOK; Gemini'ye özgü hiçbir şey burada bilinmiyor.
SQL de YOK; veritabanına yalnız database.py fonksiyonlarıyla dokunuluyor.

Neden bu dosya proje KÖKÜNDE (app/ içinde değil)?
Python, çalıştırılan script'in bulunduğu klasörü import arama yollarına
(sys.path) ekler. Script proje/app/ içinde olsaydı "from app..." importu
proje/app/app/ gibi var olmayan bir yolu arayıp hata verebilirdi. Script
kökte (proje/) durunca app/ klasörü doğrudan görünür ve importlar sorunsuz
çözülür. app/ klasörü ise uygulamanın yardımcı modüllerini barındırır.
"""

import logging

import streamlit as st
from dotenv import load_dotenv

# "as db": fonksiyonlar db.list_users() şeklinde çağrılır; okurken hangi
# modülden geldikleri hemen belli olur.
from app import database as db
from app.llm_client import LLMError, ask_llm_with_history
from app.memory import extract_memory
from app.prompt_builder import build_system_prompt

# YENİ KAVRAM — logging: Python'un standart kayıt modülü (ek kurulum yok).
# Beklenmeyen hataların traceback'i kullanıcıya değil TERMİNALE yazılsın diye
# kullanıyoruz (bkz. aşağıdaki "except Exception" bloğu).
logger = logging.getLogger(__name__)

# .env dosyasındaki değişkenleri (GEMINI_API_KEY vb.) ortam değişkeni yapar.
# Script her etkileşimde baştan çalıştığı için bu satır da her seferinde
# çalışır; zararı yok, zaten yüklü değerlerin üzerine yazmaz.
load_dotenv()

# Tablolar yoksa oluşturur. Bu da her etkileşimde çalışır; dört tablo da
# CREATE TABLE IF NOT EXISTS ile kurulduğu için tablolar varsa hiçbir şey yapmaz.
db.init_db()

# LLM'e her istekte geçmişin tamamı değil, yeni mesaj DAHİL en fazla son 20
# mesaj gönderilir (yaklaşık 10 soru-cevap turu). Sebep: API durumsuz; geçmiş
# her istekte yeniden gönderildiği için sohbet uzadıkça token maliyeti artar.
MAX_HISTORY_MESSAGES = 20

st.set_page_config(page_title="AI Chatbot — Aşama 4")
st.title("Kişiselleştirilmiş AI Chatbot")
st.caption("Aşama 4: Cevaplar profile ve hafızaya göre kişiselleşiyor; sohbet kalıcı.")

# ---------------------------------------------------------------------------
# 1) Sohbet durumu
# ---------------------------------------------------------------------------
# Script her etkileşimde baştan çalıştığı için normal bir değişken
# ("messages = []") her seferinde sıfırlanırdı. st.session_state ise sekme
# açık kaldığı sürece yaşayan bir sözlük; sohbeti orada tutuyoruz.
#
# Mesaj formatı BİLEREK llm_client'ın beklediğiyle aynı:
#     {"role": "user" | "assistant", "content": "..."}
# Böylece listeyi hiç dönüştürmeden ask_llm_with_history'ye verebiliyoruz.

if "messages" not in st.session_state:
    st.session_state.messages = []
if "selected_user_id" not in st.session_state:
    # Seçili kullanıcının id'si; None = kimse seçili değil.
    st.session_state.selected_user_id = None
if "conversation_id" not in st.session_state:
    # Aktif konuşmanın veritabanındaki id'si. None = ortada konuşma yok;
    # ilk mesaj gönderilince save_exchange yenisini açar.
    st.session_state.conversation_id = None


# ---------------------------------------------------------------------------
# 2) Kenar çubuğu: kullanıcı seçimi, yeni kullanıcı, profil, hafıza, yeni konuşma
# ---------------------------------------------------------------------------
# Selectbox'larda sunulan hazır seçenekler. Veritabanı bu değerleri ZORLAMIYOR
# (kolonlar düz TEXT); kısıt yalnızca arayüzde. Seçenek değiştirmek için şema
# değişikliği gerekmez.
LEVELS = ["Başlangıç", "Orta", "İleri"]
LENGTHS = ["Kısa", "Orta", "Detaylı"]

with st.sidebar:
    st.header("Kullanıcı")

    users = db.list_users()
    names = {u["id"]: u["name"] for u in users}

    # --- Kullanıcı seçimi ---------------------------------------------------
    # Seçenekler id listesi; None "kimse seçili değil" demek. format_func
    # kutuda id yerine ismi gösterir (saklanan değer yine id).
    options = [None] + [u["id"] for u in users]

    # Selectbox'ın kayıtlı seçimi göstermeye devam etmesi için başlangıç
    # index'ini her rerun'da session_state'ten kendimiz hesaplıyoruz.
    current = st.session_state.selected_user_id
    secilen_id = st.selectbox(
        "Aktif kullanıcı",
        options,
        index=options.index(current) if current in options else 0,
        format_func=lambda uid: "— Kullanıcı seç —" if uid is None else names[uid],
    )

    # Seçim değiştiyse: kaydet ve o kullanıcının kaldığı yeri yükle.
    if secilen_id != st.session_state.selected_user_id:
        st.session_state.selected_user_id = secilen_id
        if secilen_id is None:
            st.session_state.conversation_id = None
            st.session_state.messages = []
        else:
            # Kullanıcının EN SON konuşmasını veritabanından yükle. Uygulama
            # kapanıp açılsa bile sohbet böylece kaldığı yerden devam eder.
            cid = db.get_latest_conversation_id(secilen_id)
            st.session_state.conversation_id = cid
            st.session_state.messages = (
                db.get_messages(cid) if cid is not None else []
            )

    if not users:
        st.info("Henüz kayıtlı kullanıcı yok. Aşağıdan ilkini oluştur.")

    # --- Yeni kullanıcı -----------------------------------------------------
    # st.form: içindeki widget'lar her tuş vuruşunda rerun TETİKLEMEZ; tüm
    # değerler ancak submit butonuna basılınca birlikte gelir.
    # clear_on_submit=True, gönderimden sonra alanları temizler.
    with st.expander("Yeni kullanıcı oluştur"):
        with st.form("create_user_form", clear_on_submit=True):
            f_name = st.text_input("İsim *")
            f_role = st.text_input("Meslek / rol", placeholder="Junior Backend Developer")
            f_level = st.selectbox("Teknik bilgi seviyesi", LEVELS)
            f_interests = st.text_input("İlgi alanları", placeholder="Python, Backend, AI")
            f_length = st.selectbox("Tercih edilen cevap uzunluğu", LENGTHS)
            f_style = st.text_input("Tercih edilen anlatım şekli", placeholder="Kısa ve örnekli anlatım")
            create_clicked = st.form_submit_button("Oluştur")

        if create_clicked:
            if not f_name.strip():
                st.error("İsim boş olamaz.")
            else:
                new_id = db.create_user(
                    name=f_name.strip(),
                    role=f_role.strip(),
                    technical_level=f_level,
                    interests=f_interests.strip(),
                    preferred_length=f_length,
                    preferred_style=f_style.strip(),
                )
                st.session_state.selected_user_id = new_id  # yeni kullanıcı seçili olsun
                st.session_state.conversation_id = None  # yeni kullanıcının henüz konuşması yok
                st.session_state.messages = []
                # Yukarıdaki selectbox bu rerun'da ESKİ listeyle çizildi;
                # yeni kullanıcıyı gösterebilmesi için script'i baştan çalıştır.
                st.rerun()

    # --- Seçili kullanıcının profili (görüntüle / düzenle) --------------------
    if st.session_state.selected_user_id is not None:
        user = db.get_user(st.session_state.selected_user_id)
        if user:
            with st.expander("Profil (görüntüle / düzenle)"):
                uid = user["id"]
                # Alanlar mevcut değerlerle DOLU gelir: görüntüleme ile
                # düzenleme aynı form. key'lere uid ekliyoruz; eklemeseydik
                # Streamlit, kullanıcı DEĞİŞTİĞİNDE bile aynı kimlikli
                # widget'ın eski içeriğini hatırlayıp yeni value'ları
                # yok sayabilirdi (widget kimliği key üzerinden ayrışır).
                with st.form(f"edit_user_form_{uid}"):
                    e_name = st.text_input("İsim *", value=user["name"], key=f"e_name_{uid}")
                    e_role = st.text_input("Meslek / rol", value=user["role"], key=f"e_role_{uid}")
                    e_level = st.selectbox(
                        "Teknik bilgi seviyesi", LEVELS,
                        index=LEVELS.index(user["technical_level"]) if user["technical_level"] in LEVELS else 0,
                        key=f"e_level_{uid}",
                    )
                    e_interests = st.text_input("İlgi alanları", value=user["interests"], key=f"e_interests_{uid}")
                    e_length = st.selectbox(
                        "Tercih edilen cevap uzunluğu", LENGTHS,
                        index=LENGTHS.index(user["preferred_length"]) if user["preferred_length"] in LENGTHS else 0,
                        key=f"e_length_{uid}",
                    )
                    e_style = st.text_input("Tercih edilen anlatım şekli", value=user["preferred_style"], key=f"e_style_{uid}")
                    save_clicked = st.form_submit_button("Kaydet")

                if save_clicked:
                    if not e_name.strip():
                        st.error("İsim boş olamaz.")
                    else:
                        db.update_user(
                            uid,
                            name=e_name.strip(),
                            role=e_role.strip(),
                            technical_level=e_level,
                            interests=e_interests.strip(),
                            preferred_length=e_length,
                            preferred_style=e_style.strip(),
                        )
                        # Başarı mesajı basmıyoruz: hemen rerun yapıyoruz
                        # (selectbox'taki isim değişmiş olabilir) ve rerun
                        # mesajı zaten silerdi. Tazelenen form geri bildirimin kendisi.
                        st.rerun()

    st.divider()

    # --- Hafıza (uzun vadeli) ------------------------------------------------
    st.subheader("Hafıza")
    if st.session_state.selected_user_id is None:
        st.caption("Hafızayı görmek için bir kullanıcı seç.")
    else:
        hafiza = db.list_memories(st.session_state.selected_user_id)
        if not hafiza:
            st.caption("Henüz kayıtlı bilgi yok. Sohbette bir tercih paylaşınca burada belirir.")
        for m in hafiza:
            # YENİ KAVRAM — st.columns: alanı yatay sütunlara böler. [5, 1]
            # genişlik oranı: sol geniş (metin), sağ dar (sil butonu).
            col_text, col_del = st.columns([5, 1])
            col_text.write(m["content"])
            # Döngüde üretilen her butona benzersiz key ŞART (profil
            # formundaki uid mantığının aynısı); yoksa widget'lar çakışır.
            if col_del.button("🗑️", key=f"mem_sil_{m['id']}", help="Bu kaydı sil"):
                db.delete_memory(m["id"])
                st.rerun()  # liste güncel halini göstersin

    st.divider()

    # KARAR: "Sohbeti temizle" yerine "Yeni konuşma". Geçmişi SİLMİYORUZ,
    # yeni konuşma başlatıyoruz. Gerekçe:
    # 1) Silmek geri dönüşsüz; tek yanlış tık tüm geçmişi götürürdü.
    # 2) Demo'nun 6. adımı zaten "YENİ konuşmada tercihi hatırla" istiyor —
    #    bu buton o adımın tam kendisi.
    # 3) ChatGPT tarzı araçlardaki alışıldık davranış da bu.
    # conversation_id'yi None yapmak yetiyor: yeni satır İLK mesajda açılır
    # (save_exchange'teki tembel oluşturma), boş konuşma birikmez.
    if st.button("Yeni konuşma"):
        st.session_state.conversation_id = None
        st.session_state.messages = []


# ---------------------------------------------------------------------------
# Aktif kullanıcı yoksa sohbeti kilitle
# ---------------------------------------------------------------------------
aktif_kullanici = (
    db.get_user(st.session_state.selected_user_id)
    if st.session_state.selected_user_id is not None
    else None
)

if aktif_kullanici is None:
    st.info("Sohbete başlamak için kenar çubuğundan bir kullanıcı seç ya da oluştur.")
else:
    st.caption(f"Aktif kullanıcı: {aktif_kullanici['name']}")

# ---------------------------------------------------------------------------
# 3) Eldeki geçmişi çiz
# ---------------------------------------------------------------------------
# Ekran her rerun'da sıfırdan kurulduğu için mesajlar tek tek yeniden çizilir.
# Çizmek, LLM'e yeniden göndermek değildir — API çağrısı sadece yeni bir
# mesaj geldiğinde yapılır.
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# ---------------------------------------------------------------------------
# 4) Yeni mesaj -> system prompt + son N geçmiş -> LLM -> kaydet
# ---------------------------------------------------------------------------
# st.chat_input sayfanın altına, gönder butonu da içeren sabit bir yazma
# alanı koyar. Bu rerun'ı yeni bir mesaj tetiklediyse metni, aksi hâlde
# None döndürür.
# disabled: kullanıcı seçilmeden yazılamasın — artık her mesajın veritabanında
# bir sahibi olmak zorunda (messages -> conversations -> users zinciri).
user_message = st.chat_input("Mesajını yaz...", disabled=aktif_kullanici is None)

if user_message:
    # 1) Kullanıcı balonunu HEMEN çiz — ama henüz ne state'e ne DB'ye yaz.
    #    Yazma işini cevap başarıyla gelince yapacağız; böylece ekran,
    #    session_state ve veritabanı hep aynı şeyi söylüyor.
    with st.chat_message("user"):
        st.markdown(user_message)

    # 2) LLM'e gidecek liste: mevcut geçmiş + yeni mesaj... ama TAMAMI değil.
    #    [-N:] dilimlemesi listenin SON N öğesini alır (N'den azsa hepsini).
    history_for_llm = st.session_state.messages + [
        {"role": "user", "content": user_message}
    ]
    recent = history_for_llm[-MAX_HISTORY_MESSAGES:]

    #    Geçmiş hep user/assistant ÇİFTLERİNDEN oluşuyor; sonuna yeni user
    #    mesajı eklendiği için toplam sayı TEK. Çift bir sayıyla (20) kesince
    #    liste, sorusu atılmış bir assistant cevabıyla başlayabiliyor.
    #    Örnek: 20 eski mesaj + 1 yeni = 21; son 20'yi alınca ilk SORU gider,
    #    ilk CEVAP başta yetim kalır. O cevabı atıyoruz: LLM'e giden geçmiş
    #    her zaman bir user mesajıyla başlar.
    #    (recent asla boş değil, en azından yeni mesaj içinde; recent[0] güvenli.)
    if recent[0]["role"] == "assistant":
        recent = recent[1:]

    # 3) System prompt'u her mesajda TAZE üret: profil az önce düzenlenmiş
    #    ya da hafızaya yeni bir kayıt girmiş olabilir.
    system_prompt = build_system_prompt(
        aktif_kullanici,
        db.list_memories(aktif_kullanici["id"]),
    )

    with st.chat_message("assistant"):
        try:
            with st.spinner("Düşünüyor..."):
                answer = ask_llm_with_history(recent, system_prompt=system_prompt)
        except LLMError as e:
            # Cevap yok -> HİÇBİR yere yazmadık: DB'de yarım kayıt yok,
            # state'te sarkan user mesajı yok (eski "art arda iki user
            # mesajı" derdi de böylece bitti). Kullanıcı tekrar sorabilir.
            st.error(str(e))
        except Exception:
            # Öngörmediğimiz bir hata olsa bile uygulama çökmesin.
            # Kullanıcıya sade mesaj; terminale TAM traceback (hata ayıklamak
            # için). logger.exception yalnızca except bloğu içinde çağrılır ve
            # o anki hatanın traceback'ini mesajın altına otomatik ekler.
            logger.exception("Cevap üretilirken beklenmeyen hata")
            st.error("Beklenmeyen bir sorun oluştu. Lütfen tekrar dene.")
        else:
            st.markdown(answer)

            # 4) Soru + cevap, veritabanına TEK transaction'da. Konuşma yoksa
            #    save_exchange kendisi açıp id'sini döndürüyor.
            cid = db.save_exchange(
                aktif_kullanici["id"],
                st.session_state.conversation_id,
                user_message,
                answer,
            )
            st.session_state.conversation_id = cid

            # 5) Ekran state'i de aynı çifti alsın — DB ile birebir.
            st.session_state.messages.append({"role": "user", "content": user_message})
            st.session_state.messages.append({"role": "assistant", "content": answer})

            # 6) Uzun vadeli hafıza: mesajda kalıcı bir tercih var mı?
            #    (İkinci, küçük bir LLM çağrısı; hata olursa sessizce geçilir.)
            fact = extract_memory(user_message)
            if fact:
                db.add_memory(aktif_kullanici["id"], fact)
                # st.toast: sağ altta birkaç saniye görünen bildirim balonu —
                # demoda "bak, hafızaya aldı" anını görünür kılıyor.
                st.toast(f"Hafızaya eklendi: {fact}")
