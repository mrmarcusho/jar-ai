import sqlite3

from core.messages import contacts


def test_normalize_phone_us_10_digit():
    assert contacts.normalize_handle("(617) 555-0142") == "+16175550142"


def test_normalize_phone_11_digit_leading_1():
    assert contacts.normalize_handle("1-617-555-0142") == "+16175550142"


def test_normalize_keeps_plus_e164():
    assert contacts.normalize_handle("+16175550142") == "+16175550142"


def test_normalize_email_lowercased():
    assert contacts.normalize_handle("Dr.Smith@Clinic.COM") == "dr.smith@clinic.com"


def test_resolve_with_map():
    cmap = {"+16175550142": "Dr. Smith"}
    assert contacts.resolve("(617) 555-0142", cmap) == "Dr. Smith"
    assert contacts.resolve("+19998887777", cmap) == "+19998887777"  # fallback raw
    assert contacts.resolve(None, cmap) == "Me"  # outgoing


def test_load_store_from_synthetic_abcddb(tmp_path):
    db = tmp_path / "AddressBook-v22.abcddb"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY,
            ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY,
            ZOWNER INTEGER, ZADDRESS TEXT);
        """
    )
    conn.execute("INSERT INTO ZABCDRECORD VALUES (1,'Jane','Smith',NULL)")
    conn.execute("INSERT INTO ZABCDPHONENUMBER VALUES (1,1,'(617) 555-0142')")
    conn.execute("INSERT INTO ZABCDEMAILADDRESS VALUES (1,1,'jane@x.com')")
    conn.commit()
    conn.close()
    cmap: dict = {}
    c = sqlite3.connect(db)
    contacts._load_store(c, cmap)
    c.close()
    assert cmap["+16175550142"] == "Jane Smith"
    assert cmap["jane@x.com"] == "Jane Smith"
