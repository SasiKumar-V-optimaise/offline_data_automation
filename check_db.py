import psycopg2

conn = psycopg2.connect('postgresql://neondb_owner:npg_o2m8qDpOlaAF@ep-winter-lab-abudn19i-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require')
cur = conn.cursor()

# Get all tables
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
tables = [t[0] for t in cur.fetchall()]
print('Tables:', tables)

# Get all materials
cur.execute('SELECT id, material_name FROM raw_materials')
materials = cur.fetchall()
print('Materials:')
for m in materials:
    print(f'  {m[0]}: {m[1]}')

conn.close()