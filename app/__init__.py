# Bu dosya "app" klasörünü bir Python paketi yapar.
# Böylece "from app.llm_client import ask_llm" şeklinde import edebiliriz.
#
# Modüller:
#   llm_client.py     -> LLM'e açılan tek kapı (Google Gemini)
#   database.py       -> SQLite'a açılan tek kapı
#   prompt_builder.py -> profil + hafıza -> system prompt metni
#   memory.py         -> mesajdan kalıcı tercih çıkarımı
