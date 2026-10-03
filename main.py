"""Aşama 1 test aracı: terminalden soru sor, LLM'in cevabını gör.

Streamlit arayüzü olmadan, LLM bağlantısını (API anahtarı, model adı)
hızlıca denemek için kullanılır.

Çalıştırma (proje kök klasöründen):
    python main.py

Çıkmak için: q (veya Ctrl+C)
"""

from dotenv import load_dotenv

from app.llm_client import LLMError, ask_llm


def main() -> None:
    # .env dosyasındaki değişkenleri (GEMINI_API_KEY vb.) ortam değişkeni
    # olarak yükler. Program başında BİR KEZ çağrılması yeterlidir.
    load_dotenv()

    print("=" * 50)
    print("Kişiselleştirilmiş AI Chatbot — Aşama 1")
    print("Sorunu yaz ve Enter'a bas. Çıkmak için: q")
    print("=" * 50)

    while True:
        try:
            user_message = input("\nSen    : ").strip()
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C veya Ctrl+Z ile çıkışta traceback basma, kibarca kapan.
            print("\nGörüşürüz!")
            break

        if user_message.lower() in {"q", "quit", "exit", "çık"}:
            print("Görüşürüz!")
            break
        if not user_message:
            continue  # boş satırda API'yi boşuna çağırma

        try:
            answer = ask_llm(user_message)
            print(f"\nChatbot: {answer}")
        except LLMError as e:
            # llm_client tüm bilinen sorunları Türkçe mesajlı LLMError'a çevirir.
            print(f"\n[HATA] {e}")
        except Exception as e:
            # Öngöremediğimiz bir şey olsa bile program çökmesin.
            print(f"\n[HATA] Beklenmeyen bir sorun oluştu: {e}")


if __name__ == "__main__":
    main()
