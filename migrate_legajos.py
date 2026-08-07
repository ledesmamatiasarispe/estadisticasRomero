"""migrate_legajos.py — Carga la tabla _personal_ext con legajo real, apellido y nombre."""
import sqlite3
from pathlib import Path

DB = Path(r"C:\GNC_API\data\gnc.db")

# (access_id, legajo_real, apellido, nombre, desvinculado)
# access_id: código interno del Access
# legajo_real: legajo del sistema de RRHH
# desvinculado: D = True (solo informativo)
DATOS = [
    # access_id  legajo  apellido             nombre          desv
    (11,         "11",   "Parejas",           "Carlos",       False),
    (16,         "16",   "Iriarte",           "Angel",        False),
    (34,         "34",   "Minetti",           "Marcelo",      False),
    (48,         "48",   "Kern",              "Daniel",       False),
    (55,         "55",   "Ibarrola",          "Andres",       True),
    (56,         "56",   "Vallejo",           "Leonides",     False),
    (59,         "59",   "Berasain",          "Betiana",      False),
    (63,         "63",   "Carabajal",         "Carlos",       True),
    (64,         "64",   "Puente",            "Sebastian",    True),
    (65,         "65",   "Rodriguez",         "Miguel",       True),
    (75,         "75",   "Romero",            "Leandro",      False),
    (83,         "83",   "Gamarra",           "Fabian",       True),
    (85,         "85",   "Bobadilla",         "Alcides",      False),
    (87,         "87",   "Bobadilla",         "Fredy",        True),
    (88,         "88",   "Periello",          "Alejandro",    False),
    (89,         "89",   "Daniel",            "Torres",       False),
    (91,         "91",   "Sayone",            "Facundo",      True),
    (92,         "92",   "Corre",             "Marcos",       True),
    (93,         "93",   "Bareto",            "Nestor",       False),
    (94,         "94",   "Diaz",              "Eber",         False),
    (95,         "95",   "Cabrera",           "Daniel",       False),
    (96,         "96",   "Castro",            "Cesar",        True),
    (97,         "97",   "Cabrera",           "Nahuel",       False),
    (98,         "98",   "Cristaldo",         "Martin",       True),
    (99,         "99",   "Ramos",             "Gustavo",      True),
    (1105,       "100",  "Londero",           "Facundo",      True),
    (1107,       "103",  "Ortiz",             "Hugo",         False),
    (1109,       "104",  "Barreto",           "Axel",         True),
    (1108,       "105",  "Gatti",             "Sergio",       False),
    (1110,       "106",  "Arispe",            "Matias",       False),
    (1114,       "107",  "Carrion",           "Lisandro",     True),
    (1116,       "108",  "Aguero",            "Renzo",        True),
    (109,        "109",  "Perez",             "Dante",        True),
    (110,        "110",  "Delgado",           "Pedro",        False),
    (1010,       "1010", "Villalba",          "Fredy",        True),
    (1093,       "1093", "Barrionuevo",       "Gonzalo",      True),
    (1097,       "1097", "Blanco",            "Omar",         True),
    (1098,       "1098", "Delvalle",          "Sila",         True),
    (1099,       "1099", "Sandoval",          "Cesar",        True),
    (1102,       "1102", "Filipelli Colletto","Noelia",       True),
    (1103,       "1103", "Diaz",              "Cristian",     True),
    (1104,       "1104", "Salinas",           "Manuel",       True),
    (1111,       "1111", "Cabrera",           "Matias",       True),
    (1112,       "1112", "Rodriguez",         "Jose",         True),
    (1113,       "1113", "Pintos",            "Alejandro",    True),
    (1115,       "1114", "Alcorta",           "Enzo",         True),
    # G/L = subcontratado sin ID en Access → ID provisional 9001
    (9001,       "G/L",  "Giordano",          "Agustin",      False),
]

conn = sqlite3.connect(DB)
try:
    # Giordano vive en _responsables_extra (no en Responsables, que es de Access)
    conn.execute(
        "INSERT OR IGNORE INTO _responsables_extra "
        "(codigoresponsable, apellidoynombre, nombre, apellido, codsector) "
        "VALUES (9001,'Giordano Agustin','Agustin','Giordano',1)"
    )
    print("Giordano Agustin asegurado en _responsables_extra")

    ok = 0
    for (access_id, legajo, apellido, nombre, _desv) in DATOS:
        conn.execute(
            "INSERT INTO _personal_ext (access_id, legajo, apellido, nombre) VALUES (?,?,?,?) "
            "ON CONFLICT(access_id) DO UPDATE SET legajo=excluded.legajo, "
            "apellido=excluded.apellido, nombre=excluded.nombre",
            (access_id, legajo, apellido, nombre)
        )
        ok += 1
    conn.commit()
    print(f"Legajos cargados: {ok}")
finally:
    conn.close()
