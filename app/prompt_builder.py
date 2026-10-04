"""Kullanıcı profili + hafıza kayıtlarından system prompt üreten modül .

Buradaki fonksiyon SAF metin işi yapar: veritabanına gitmez, LLM'i çağırmaz.
Girdi olarak hazır dict/list alır, çıktı olarak düz metin döndürür. Bu sayede
test etmesi çok kolay: print(build_system_prompt(...)) yeterli.

Prompt'un TÜM metni aşağıdaki sabitlerde duruyor; üslubu değiştirmek istersen
sadece sabitleri düzenlemek yeterli olacaktır.
"""

# ---------------------------------------------------------------------------
# ŞABLON — prompt metninin tamamı tek yerde
# ---------------------------------------------------------------------------
# {profile_block} ve {memory_block}, build_system_prompt içinde .format(...)
# ile doldurulan yer tutucular. DİKKAT: şablona süslü parantezli başka metin
# ekleyeceksen {{ }} şeklinde çiftlemen gerekir, yoksa .format onu da yer
# tutucu sanıp hata verir.
SYSTEM_TEMPLATE = """Sen yardımsever bir asistansın. Sorulara Türkçe, açık ve anlaşılır cevaplar ver.

Karşındaki kullanıcının profili aşağıda. Bunlar arka plan bilgisi değil, cevabını şekillendiren kurallardır:
{profile_block}{memory_block}
Nasıl cevap vereceksin:
- Teknik seviyeye uy. Başlangıç seviyesindeki birine terimi önce açıkla veya benzetmeyle anlat; 
  ileri seviyedeki birine temel tanımları tekrarlayıp vakit kaybettirme.
- Uzunluk tercihine uy. Kısa isteniyorsa gereksiz giriş ve kapanış cümlesi kurma; 
  detaylı isteniyorsa uç durumlara, maliyetlere ve alternatiflere de gir.
- Örnek verirken kullanıcının ilgi alanlarından ve kullandığı araçlardan seç.
- Profili kullanıcıya geri anlatma. "Sen junior geliştirici olduğun için..." gibi cümleler kurma; 
  bilgiyi sessizce uygula.
- Kullanıcı o anki mesajında farklı bir şey isterse profilden önce o gelir. 
  Profilinde "Kısa" yazan biri "bunu uzun uzun anlat" derse uzun anlat.
- Profilde olmayan bir şeyi biliyormuş gibi davranma."""

# Profil satırları: (prompt'ta görünecek etiket, users tablosundaki kolon adı).
# Profile yeni bir alan eklersen buraya bir satır eklemen yeterli.
_PROFILE_FIELDS = [
    ("İsim", "name"),
    ("Meslek / rol", "role"),
    ("Teknik bilgi seviyesi", "technical_level"),
    ("İlgi alanları", "interests"),
    ("Tercih edilen cevap uzunluğu", "preferred_length"),
    ("Tercih edilen anlatım şekli", "preferred_style"),
]

_MEMORY_HEADER = "\nÖnceki konuşmalardan akılda tutulan kalıcı bilgiler:\n"


def build_system_prompt(user: dict, memories: list[dict] | None = None) -> str:
    """Profil ve hafıza kayıtlarını tek bir system prompt metnine çevirir.

    user     : database.get_user(...) çıktısı (düz dict).
    memories : database.list_memories(...) çıktısı; None ya da boş olabilir.

    Boş ("") veya None alanlar prompt'a HİÇ yazılmaz — "İsim: " gibi sarkık,
    yarım satırlar oluşmaz. (user.get(key) or "") ifadesi hem eksik anahtarı
    hem None değeri boş metne çevirir; strip() sadece boşluk girilmiş alanı
    da eler.
    """
    profile_lines = []
    for label, key in _PROFILE_FIELDS:
        value = (user.get(key) or "").strip()
        if value:  # boşsa satırı tamamen atla
            profile_lines.append(f"- {label}: {value}")

    if not profile_lines:
        # Tüm alanlar boşsa bile şablon bozulmasın diye tek satırlık dolgu.
        profile_lines.append("- (Profil bilgisi girilmemiş)")

    memory_block = ""
    if memories:
        memory_lines = [f"- {m['content']}" for m in memories]
        memory_block = _MEMORY_HEADER + "\n".join(memory_lines) + "\n"

    return SYSTEM_TEMPLATE.format(
        profile_block="\n".join(profile_lines) + "\n",
        memory_block=memory_block,
    )