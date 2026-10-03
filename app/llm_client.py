"""LLM sağlayıcısıyla konuşan TEK modül — Google Gemini sürümü.

Uygulamanın geri kalanı LLM'e yalnızca bu modüldeki fonksiyonlar üzerinden
erişir ve Gemini'ye özgü hiçbir şey bilmez. Sağlayıcı değiştirmek istersek
sadece bu dosyanın İÇİ değişir; ask_llm() / ask_llm_with_history() imzaları
aynı kaldığı sürece projenin geri kalanına dokunulmaz.

SAĞLAYICI DEĞİŞİKLİĞİ NOTU (OpenAI -> Gemini)
---------------------------------------------
Proje OpenAI ile başladı, sonra Gemini'ye taşındı. LLM'e tek kapıdan
eriştiğimiz için taşımada yalnızca bu dosya yeniden yazıldı; diğer dosyalarda
tek satır değişmedi. İki sağlayıcı arasındaki farkların tablosu README'deki
"Sağlayıcı değişikliği" bölümünde.
"""

import os

# Gemini SDK'sı ağ/zaman aşımı hatalarını kendi sınıflarına çevirmiyor, alttaki
# httpx kütüphanesinin ham hatası olarak fırlatıyor; anlamlı mesaj verebilmek
# için httpx'i doğrudan import ediyoruz. httpx google-genai ile birlikte
# kuruluyor, ama doğrudan kullandığımız için requirements.txt'de de listelendi.
import httpx
from google import genai
from google.genai import errors, types

# .env'de GEMINI_MODEL tanımlı değilse ya da boş bırakılmışsa kullanılacak model.
#
# Neden Flash-Lite? Ucuz, hızlı ve "düşünme" (thinking) modu varsayılan olarak
# en düşük seviyede — bir sohbet botu için ideal. gpt-4o-mini'nin Gemini
# tarafındaki karşılığı diyebiliriz.
#
# DİKKAT: Google model isimlerini sık değiştiriyor ve eskilerini kapatıyor.
# Bir gün "404 model bulunamadı" hatası alırsan panik yapma; güncel listeye
# https://ai.google.dev/gemini-api/docs/models adresinden bak ve .env'deki
# GEMINI_MODEL satırını güncelle — kodu değiştirmene gerek yok.
DEFAULT_MODEL = "gemini-3.5-flash-lite"

# Cevabın üst uzunluk sınırı (OpenAI'deki max_tokens'ın karşılığı).
#
# NEDEN 4096 gibi "bol" bir sayı? Gemini 3 ailesindeki modeller cevabı yazmadan
# önce içeriden "düşünebiliyor" ve bu düşünme tokenları da bu bütçeden yeniyor.
# Sınırı 200-300 gibi düşük tutarsan model bütçeyi düşünürken bitirip BOŞ cevap
# döndürebilir. Cevabın kısa olmasını profil/hafıza (system prompt) sağlıyor
# zaten; bu sayı sadece bir güvenlik tavanı.
MAX_OUTPUT_TOKENS = 4096

# İstek zaman aşımı. DİKKAT: Gemini SDK'sında bu değer MİLİSANİYE cinsindendir
# (OpenAI'de saniyeydi). 60_000 = 60 saniye.
TIMEOUT_MS = 60_000

DEFAULT_SYSTEM_PROMPT = (
    "Sen yardımsever bir asistansın. "
    "Sorulara Türkçe, açık ve anlaşılır cevaplar ver."
)


class LLMError(Exception):
    """Kullanıcıya doğrudan gösterilebilecek, anlaşılır bir hata mesajı taşır.

    Google kütüphanesinin kendi hata sınıflarını (ClientError, ServerError...)
    uygulamanın geri kalanına sızdırmamak için hepsini bu tek tipe çeviriyoruz.
    Böylece main.py ve streamlit_app.py sadece LLMError'ı tanımaya devam eder —
    sağlayıcı değişse bile o dosyalardaki except blokları aynı kalır.
    """


def _to_gemini_contents(messages: list[dict]) -> list[types.Content]:
    """Projenin ortak mesaj formatını Gemini'nin beklediği biçime çevirir.

    Projede her yerde (session_state, messages tablosu, llm_client) kullandığımız
    format:
        {"role": "user" | "assistant", "content": "..."}

    Gemini ise "assistant" yerine "model" diyor ve metni "parts" listesine
    sarıyor:
        Content(role="user" | "model", parts=[Part(text="...")])

    Bu çeviriyi burada, TEK yerde yapıyoruz. Böylece "model" kelimesi bu
    dosyanın dışına hiç çıkmıyor; veritabanı ve arayüz Gemini'yi hiç duymuyor.
    """
    contents = []
    for m in messages:
        # "user" dışındaki her şeyi "model" sayıyoruz; listede zaten sadece
        # user/assistant var (system mesajı ayrı alana gidiyor, aşağıya bak).
        rol = "model" if m["role"] == "assistant" else "user"
        contents.append(
            types.Content(role=rol, parts=[types.Part.from_text(text=m["content"])])
        )
    return contents


def ask_llm_with_history(
    messages: list[dict],
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> str:
    """Sohbet geçmişini LLM'e gönderir, modelin cevabını düz metin döndürür.

    messages, eskiden yeniye sıralı bir listedir; her öğe şu biçimdedir:
        {"role": "user" | "assistant", "content": "..."}
    Sistem mesajını bu fonksiyon kendisi ekler; listede OLMAMALIDIR.

    Dönüş değeri her zaman DOLU bir metindir. Sorun çıkarsa (boş cevap dahil)
    Türkçe ve anlaşılır bir mesajla LLMError fırlatır; programı çökertecek ham
    hata (traceback) dışarı sızmaz.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError(
            "API anahtarı bulunamadı. Proje klasöründe bir .env dosyası oluşturup "
            "içine GEMINI_API_KEY=... satırını eklemelisin (example.env dosyasına bak). "
            "Anahtarı https://aistudio.google.com/apikey adresinden ücretsiz alabilirsin."
        )

    # Gemini boş bir mesaj listesini kabul etmez; kendi hatamızı erken verelim.
    if not messages:
        raise LLMError("Gönderilecek mesaj yok.")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS),
    )

    # "or" kullanıyoruz ki .env'de GEMINI_MODEL= şeklinde boş bırakılırsa
    # (getenv boş metin döndürür) yine varsayılan modele düşelim.
    model = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL

    try:
        response = client.models.generate_content(
            model=model,
            # Sadece sohbet geçmişi. OpenAI'de system mesajı bu listenin ilk
            # elemanıydı; Gemini'de listeye DEĞİL, aşağıdaki config'e giriyor.
            contents=_to_gemini_contents(messages),
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                # NOT: temperature'ı bilerek AYARLAMIYORUZ. Google, Gemini 3
                # ailesinde varsayılan değerin (1.0) korunmasını öneriyor;
                # düşürmek uzun/karmaşık isteklerde modeli döngüye sokabiliyor.
                # (OpenAI sürümünde temperature=0.7 yazıyorduk.)
                #
                # Otomatik fonksiyon çağırma (AFC) bu projede kullanılmıyor.
                # Kapatmasak da çalışır ama SDK her istekte terminale bir uyarı
                # basıyor; kapatınca çıktı tertemiz kalıyor.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )

        # response.text, cevaptaki metin parçalarını birleştirip verir.
        # Cevap güvenlik filtresine takılırsa ya da token bütçesi biterse
        # None dönebilir; "or" onu boş metne çeviriyor.
        # strip()'i kontrolden ÖNCE yapıyoruz: sadece boşluktan oluşan bir
        # cevap ("   ") da boş sayılsın. Sırası ters olsaydı "   " kontrolü
        # geçer (boş olmayan metin True sayılır), strip() sonrası "" dönerdi.
        answer = (response.text or "").strip()
        if not answer:
            raise LLMError(
                "Model boş bir cevap döndürdü. Güvenlik filtresine takılmış ya da "
                "cevap uzunluk sınırına çarpmış olabilir. Lütfen tekrar dene."
            )
        return answer

    # --- Ağ katmanı hataları -------------------------------------------------
    # Bunlar Gemini'nin değil, alttaki httpx kütüphanesinin hatalarıdır.
    # DİKKAT: TimeoutException, RequestError'ın alt sınıfıdır -> önce timeout.
    except httpx.TimeoutException:
        raise LLMError(
            f"LLM {TIMEOUT_MS // 1000} saniye içinde cevap vermedi (zaman aşımı). Tekrar dene."
        )
    except httpx.RequestError:
        raise LLMError(
            "Gemini API'sine bağlanılamadı. İnternet bağlantını kontrol et; "
            "güvenlik duvarı veya proxy engelliyor olabilir."
        )

    # --- API hataları --------------------------------------------------------
    # OpenAI'de her durum için ayrı bir sınıf vardı (AuthenticationError,
    # RateLimitError...). Gemini SDK'sında sadece iki alt sınıf var:
    #   ClientError -> 4xx (hata BİZDE: anahtar, model adı, limit...)
    #   ServerError -> 5xx (hata GOOGLE'DA)
    # Ayrımı HTTP kodundan (e.code) kendimiz yapıyoruz.
    # Yakalama sırası önemli: ClientError/ServerError, APIError'ın alt
    # sınıflarıdır -> genel APIError en sona.
    except errors.ClientError as e:
        kod = getattr(e, "code", None)
        if kod in (401, 403):
            raise LLMError(
                "API anahtarı geçersiz ya da yetkisiz. .env dosyasındaki GEMINI_API_KEY "
                "değerini kontrol et; anahtar yanlış kopyalanmış, iptal edilmiş veya "
                "bulunduğun bölgede kullanıma kapalı olabilir."
            )
        if kod == 400:
            # 400 = "istek hatalı". Gemini geçersiz anahtarda da 400 döndürüyor,
            # ama TEK sebep bu değil (hatalı parametre, desteklenmeyen ayar...).
            # Hepsine "anahtar yanlış" demek hata ararken yanlış yere baktırırdı;
            # bu yüzden sunucunun kendi mesajını da ekliyoruz.
            raise LLMError(
                "İstek geçersiz bulundu (HTTP 400). En sık sebep hatalı bir API "
                "anahtarıdır; .env'deki GEMINI_API_KEY değerini kontrol et. "
                f"Sunucunun mesajı: {getattr(e, 'message', e)}"
            )
        if kod == 404:
            raise LLMError(
                f"'{model}' adında bir model bulunamadı. Google model isimlerini sık "
                "değiştiriyor; güncel listeyi ai.google.dev/gemini-api/docs/models "
                "adresinden kontrol edip .env'deki GEMINI_MODEL değerini güncelle."
            )
        if kod == 429:
            raise LLMError(
                "İstek limiti aşıldı. Biraz bekleyip tekrar dene. "
                "(Ücretsiz katmanın dakikalık/günlük kotası dolduysa da bu hata gelir; "
                "Google AI Studio'dan kotanı kontrol edebilirsin.)"
            )
        raise LLMError(f"API isteği reddedildi (HTTP {kod}): {getattr(e, 'message', e)}")
    except errors.ServerError as e:
        raise LLMError(
            f"Gemini sunucusunda geçici bir sorun var (HTTP {getattr(e, 'code', '5xx')}). "
            "Bu hata bizim kodumuzdan kaynaklanmıyor; birkaç saniye sonra tekrar dene."
        )
    except errors.APIError as e:
        # Yukarıdakilere uymayan her türlü API hatası için son güvenlik ağı.
        raise LLMError(f"Beklenmeyen bir LLM hatası oluştu: {e}")


def ask_llm(user_message: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> str:
    """Tek mesajlık kısayol — main.py ve memory.py bunu kullanıyor.

    Asıl iş ask_llm_with_history'de. Burası tek mesajı tek elemanlı bir listeye
    sarıp ona devrediyor. Böylece istemci kurulumu ve hata yakalama tek yerde
    kalıyor (kopya kod yok).
    """
    return ask_llm_with_history(
        [{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
    )
