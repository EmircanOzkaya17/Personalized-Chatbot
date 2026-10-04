"""Uzun vadeli hafıza: kullanıcı mesajından kalıcı tercih/bilgi çıkarımı (Aşama 4).

Yaklaşım — "LLM'e sor" (en basit ÇALIŞAN yol):
Her kullanıcı mesajından sonra LLM'e küçük bir ek istek atıp "bu mesajda
kalıcı bir bilgi var mı?" diye soruyoruz. Varsa tek cümle döner, onu
memories tablosuna yazarız; yoksa model YOK der, hiçbir şey yapmayız.

Bedeli: her kullanıcı mesajı için bir ek API çağrısı (maliyet ve bekleme
süresi artar).
"""

from app.llm_client import LLMError, ask_llm

# Çıkarım modelinin talimatı. Model burada sohbet ETMEZ; ya tek cümlelik
# bilgi ya da YOK döndürür. "Başka hiçbir şey yazma" gibi kesin komutlar,
# modelin cevabına açıklama ekleme huyunu bastırmak için.
_EXTRACTION_PROMPT = """Görevin, kullanıcının mesajında GELECEKTEKİ konuşmalarda da işe yarayacak kalıcı bir bilgi ya da tercih olup olmadığını bulmak.

Kalıcı bilgi örnekleri:
- "bundan sonra daha kısa cevap ver" -> Kullanıcı daha kısa cevaplar istiyor.
- "React kullanıyorum" -> Kullanıcı React kullanıyor.
- "ben görerek öğrenirim" -> Kullanıcı görsel örneklerle öğrenmeyi tercih ediyor.

Kalıcı OLMAYANLAR: sorular, selamlaşmalar, tek seferlik istekler ("bu cevabı biraz kısalt" gibi).

Kalıcı bilgi VARSA: bilgiyi "Kullanıcı ..." ile başlayan TEK kısa cümle olarak yaz.
YOKSA: sadece YOK yaz.
Başka hiçbir şey yazma."""


def extract_memory(user_message: str) -> str | None:
    """Mesajda kalıcı bilgi varsa onu tek cümle olarak döndürür, yoksa None.

    Hafıza çıkarımı "olsa güzel olur" bir özellik; sohbetin kendisi değil.
    Bu yüzden buradaki bir LLM hatasını kullanıcıya GÖSTERMİYORUZ, sessizce
    None döndürüyoruz — asıl cevap zaten ekrana gelmiş durumda.
    """
    try:
        answer = ask_llm(user_message, system_prompt=_EXTRACTION_PROMPT)
    except LLMError:
        return None

    # Model bazen tırnak ya da fazladan satır ekleyebilir; ilk satırı al,
    # baştaki/sondaki boşluk ve tırnakları temizle.
    # [0] güvenli: ask_llm boş cevapta LLMError fırlattığı için answer'da
    # en az bir dolu satır olduğu garanti.
    fact = answer.strip().splitlines()[0].strip().strip('"')
    if not fact or fact.upper().startswith("YOK"):
        return None
    return fact
