import sqlite3

def generate_mock_data():
    conn = sqlite3.connect("structured_db/structured.db")
    
    nodes = [
        ('A1', 'Microservices', 'Architecture'), ('A2', 'Docker', 'Technology'), ('A3', 'Kubernetes', 'Technology'),
        ('B1', 'Electroplating', 'Process'), ('B2', 'Anode', 'Component'), ('B3', 'Electrolyte', 'Material'),
        ('C1', 'Revenue', 'Finance'), ('C2', 'EBITDA', 'Finance'), ('C3', 'Margin', 'Finance'),
    ]
    edges = [
        ('A1', 'A2', 'uses'), ('A2', 'A3', 'managed by'), ('A1', 'A3', 'deployed on'),
        ('B1', 'B2', 'requires'), ('B2', 'B3', 'immersed in'), ('B1', 'B3', 'consumes'),
        ('C1', 'C2', 'calculates'), ('C2', 'C3', 'derives'), ('C1', 'C3', 'impacts'),
        ('A1', 'C1', 'generates') # Weak cross edge
    ]
    
    for id, label, cat in nodes:
        conn.execute("INSERT OR REPLACE INTO kg_nodes (id, label, type, description) VALUES (?, ?, ?, 'desc')", (id, label, cat))
    for src, tgt, rel in edges:
        conn.execute("INSERT OR REPLACE INTO kg_edges (source, target, relation, description, source_doc, workspace_id) VALUES (?, ?, ?, 'desc', 'mock.pdf', 'default')", (src, tgt, rel))
    conn.commit()

generate_mock_data()
