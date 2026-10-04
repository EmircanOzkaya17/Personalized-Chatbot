"""Veritabanıyla konuşan TEK modül.

llm_client.py LLM için neyse, bu dosya SQLite için o: uygulamanın geri
kalanı SQL yazmaz, sqlite3 import etmez; buradaki fonksiyonları çağırır ve
karşılığında düz Python sözlükleri (dict) alır. Veritabanını değiştirmek
istersek (örn. SQLite -> PostgreSQL) sadece bu dosyanın içi değişir.

Tablolar ve ilişkileri (foreign key ile birbirine bağlı):

    users
      ├── conversations      kısa vadeli hafıza: sohbetler...
      │       └── messages   ...ve içlerindeki mesajlar
      └── memories           uzun vadeli hafıza: her sohbette system
                             prompt'a eklenen kalıcı bilgiler

"Yeni konuşma" butonu eski sohbeti SİLMEZ: eski kayıtlar veritabanında
durur, arayüz yalnızca aktif conversation_id'yi None yapar. Bu yüzden eski
sohbet yeni sohbeti etkilemez.

İlk kurulum + örnek kullanıcılar için (proje kökünden):
    python -m app.database
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------------------
# Veritabanı dosyasının yolu — TEK sabit
# ---------------------------------------------------------------------------
# __file__ bu dosyanın yolu (app/database.py); iki .parent ile proje köküne
# çıkıyoruz. Böylece uygulama HANGİ klasörden başlatılırsa başlatılsın dosya
# hep aynı yerde durur. Düz "chatbot.db" yazsaydık dosya, komutu çalıştırdığın
# klasöre göre farklı yerlerde oluşurdu.
# Not: .gitignore'daki *.db kalıbı bu dosyayı zaten kapsıyor, git'e girmez.
DB_PATH = Path(__file__).resolve().parent.parent / "chatbot.db"


@contextmanager
def _connect():
    """Bağlan -> işlemleri yap -> hatasızsa COMMIT, hata varsa ROLLBACK -> kapat."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # satırlara kolon ADIYLA erişim: row["name"]
    # SQLite'ta FOREIGN KEY denetimi varsayılan KAPALIDIR ve bağlantı başına
    # açılması gerekir. conversations / messages / memories tablolarındaki
    # ilişkiler ancak bu satır sayesinde gerçekten denetleniyor.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:        # blok hatasız biterse COMMIT, hata olursa ROLLBACK
            yield conn
    finally:
        conn.close()      # "with conn" bağlantıyı KAPATMAZ, sadece işlemi yönetir


def init_db() -> None:
    """Tabloları (yoksa) oluşturur; tekrar tekrar çağrılması güvenlidir.

    IF NOT EXISTS: tablo zaten varsa komut sessizce hiçbir şey yapmaz. Bu
    sayede uygulamanın her açılışında güvenle çağrılabilir.
    """
    with _connect() as conn:
        # Ana tablo: diğer üç tablo buna bağlanıyor, o yüzden ilk sırada.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT NOT NULL,
                role             TEXT NOT NULL DEFAULT '',
                technical_level  TEXT NOT NULL DEFAULT '',
                interests        TEXT NOT NULL DEFAULT '',
                preferred_length TEXT NOT NULL DEFAULT '',
                preferred_style  TEXT NOT NULL DEFAULT '',
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # -------------------------------------------------------------------
        # YENİ KAVRAM — REFERENCES (foreign key / FK): "user_id, users
        # tablosundaki bir id olmak ZORUNDA" demek; var olmayan kullanıcıya
        # konuşma bağlanamaz. ON DELETE CASCADE: bir kullanıcı silinirse ona
        # bağlı satırlar da otomatik silinir, öksüz kayıt kalmaz. Bu denetim,
        # _connect içinde açtığımız PRAGMA foreign_keys sayesinde çalışıyor.
        # -------------------------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT NOT NULL,    -- 'user' | 'assistant' (ortak mesaj formatıyla birebir)
                content         TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )


# ---------------------------------------------------------------------------
# Kullanıcılar (Aşama 3 — profil)
# ---------------------------------------------------------------------------

def create_user(
    name: str,
    role: str = "",
    technical_level: str = "",
    interests: str = "",
    preferred_length: str = "",
    preferred_style: str = "",
) -> int:
    """Yeni kullanıcı ekler ve veritabanının verdiği id'yi döndürür.

    Sorgudaki ? işaretleri "yer tutucu"dur: değerler SQL metnine
    yapıştırılmaz, ikinci argümandaki demetten AYRI bir kanaldan gönderilir
    (parametreli sorgu). Böylece kullanıcının yazdığı metin asla SQL komutu
    olarak yorumlanamaz; bu, SQL injection saldırılarına karşı korur.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (name, role, technical_level, interests,
                               preferred_length, preferred_style)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, role, technical_level, interests,
             preferred_length, preferred_style),
        )
        return cur.lastrowid  # az önce eklenen satırın id'si


def get_user(user_id: int) -> dict | None:
    """id ile tek kullanıcı getirir; bulunamazsa None döndürür."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),  # DİKKAT: tek elemanlı demet — sondaki virgül şart!
        ).fetchone()
        # sqlite3.Row -> düz dict: dışarıya sqlite'a özgü tip sızdırmıyoruz,
        # llm_client'ın Gemini tiplerini sızdırmaması gibi.
        return dict(row) if row else None


def list_users() -> list[dict]:
    """Bütün kullanıcıları eskiden yeniye sıralı, düz dict listesi döndürür."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def update_user(
    user_id: int,
    name: str,
    role: str,
    technical_level: str,
    interests: str,
    preferred_length: str,
    preferred_style: str,
) -> None:
    """Kullanıcının TÜM profil alanlarını günceller.

    Arayüzdeki düzenleme formu zaten bütün alanları dolu gönderdiği için
    "sadece değişen alanı güncelle" karmaşasına girmedik; hepsi birden yazılır.
    """
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
               SET name = ?, role = ?, technical_level = ?, interests = ?,
                   preferred_length = ?, preferred_style = ?
             WHERE id = ?
            """,
            (name, role, technical_level, interests,
             preferred_length, preferred_style, user_id),
        )


# ---------------------------------------------------------------------------
# Konuşmalar ve mesajlar (Aşama 4 — kısa vadeli hafıza)
# ---------------------------------------------------------------------------

def get_latest_conversation_id(user_id: int) -> int | None:
    """Kullanıcının EN SON konuşmasının id'si; hiç konuşması yoksa None.

    Uygulama açılınca "kaldığı yerden devam" bununla oluyor: son konuşmayı
    bul, mesajlarını yükle.
    """
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM conversations
             WHERE user_id = ?
             ORDER BY id DESC
             LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return row["id"] if row else None


def get_messages(conversation_id: int) -> list[dict]:
    """Bir konuşmanın TÜM mesajlarını eskiden yeniye döndürür.

    Sadece role ve content seçiyoruz: dönen liste, uygulamadaki ortak mesaj
    formatının ({"role": ..., "content": ...}) birebir kendisi; dönüşümsüz
    hem st.session_state'e hem ask_llm_with_history'ye verilebilir.
    (created_at yerine id'ye göre sıralıyoruz: created_at saniye
    hassasiyetinde, aynı saniyedeki soru-cevabın sırası karışabilirdi;
    id ise her eklemede kesin artar.)
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
             WHERE conversation_id = ?
             ORDER BY id
            """,
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_exchange(
    user_id: int,
    conversation_id: int | None,
    user_content: str,
    assistant_content: str,
) -> int:
    """Bir soru-cevap çiftini TEK işlemde kaydeder; konuşma id'sini döndürür.

    conversation_id None ise önce yeni konuşma açılır ("tembel oluşturma":
    'Yeni konuşma' butonu satır AÇMAZ, satır ilk mesaj gelince açılır —
    böylece veritabanında bomboş konuşmalar birikmez).

    Neden üç INSERT tek fonksiyonda? Hepsi aynı "with _connect()" bloğunda,
    yani aynı TRANSACTION içinde: blok hatasız biterse hepsi birden yazılır
    (COMMIT), herhangi biri patlarsa HİÇBİRİ yazılmaz (ROLLBACK). Cevapsız
    soru gibi yarım kayıt oluşması bu sayede imkânsız.
    """
    with _connect() as conn:
        if conversation_id is None:
            cur = conn.execute(
                "INSERT INTO conversations (user_id) VALUES (?)", (user_id,)
            )
            conversation_id = cur.lastrowid
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
            (conversation_id, user_content),
        )
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
            (conversation_id, assistant_content),
        )
        return conversation_id


# ---------------------------------------------------------------------------
# Hafıza kayıtları (Aşama 4 — uzun vadeli hafıza)
# ---------------------------------------------------------------------------

def add_memory(user_id: int, content: str) -> None:
    """Kullanıcıya bir hafıza kaydı ekler; birebir aynısı zaten varsa eklemez.

    Kopya kontrolü seed_demo_users'taki mantığın aynısı: LLM aynı tercihi
    iki konuşmada da çıkarırsa tablo aynı cümleyle dolmasın.
    """
    with _connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM memories WHERE user_id = ? AND content = ?",
            (user_id, content),
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO memories (user_id, content) VALUES (?, ?)",
                (user_id, content),
            )


def list_memories(user_id: int) -> list[dict]:
    """Kullanıcının hafıza kayıtlarını eskiden yeniye döndürür (id, content, created_at)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, content, created_at FROM memories WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_memory(memory_id: int) -> None:
    """Tek bir hafıza kaydını siler (kenar çubuğundaki çöp kutusu butonu)."""
    with _connect() as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))


# ---------------------------------------------------------------------------
# Demo verisi
# ---------------------------------------------------------------------------
# Şartnamedeki demo senaryosu için farklı teknik seviyede iki kullanıcı.
_DEMO_USERS = [
    {
        "name": "Ahmet",
        "role": "Junior Backend Developer",
        "technical_level": "Başlangıç",
        "interests": "Python, Backend, AI",
        "preferred_length": "Kısa",
        "preferred_style": "Örnekli ve sade anlatım",
    },
    {
        "name": "Zeynep",
        "role": "Senior Data Scientist",
        "technical_level": "İleri",
        "interests": "Makine öğrenmesi, istatistik, dağıtık sistemler",
        "preferred_length": "Detaylı",
        "preferred_style": "Teknik terimli, derinlemesine anlatım",
    },
]


def seed_demo_users() -> None:
    """Demo için iki örnek kullanıcı ekler; tekrar çalıştırılırsa kopyalamaz.

    Kopya kontrolü isimle yapılıyor: aynı isimde kayıt varsa o kullanıcı
    atlanır. users.name'e UNIQUE kısıtı bilerek KOYMADIK — gerçek hayatta iki
    kişinin adı aynı olabilir; tekrar koruması yalnızca bu fonksiyonun işi.
    """
    with _connect() as conn:
        for u in _DEMO_USERS:
            exists = conn.execute(
                "SELECT 1 FROM users WHERE name = ?", (u["name"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO users (name, role, technical_level, interests,
                                   preferred_length, preferred_style)
                VALUES (:name, :role, :technical_level, :interests,
                        :preferred_length, :preferred_style)
                """,
                u,  # ":ad" yer tutucuları, değerleri bu sözlükten anahtar adıyla alır
            )


if __name__ == "__main__":
    # Proje kökünden:  python -m app.database
    # (-m, dosyayı "app" paketinin bir modülü olarak çalıştırır; importlar
    #  böylece doğru çözülür.)
    init_db()
    seed_demo_users()
    print(f"Veritabanı hazır: {DB_PATH}")
    for u in list_users():
        print(f"  #{u['id']}  {u['name']}  (seviye: {u['technical_level'] or '-'})")
