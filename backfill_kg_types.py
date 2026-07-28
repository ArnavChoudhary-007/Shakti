import sqlite3
import json
import httpx
import asyncio

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:1b"

VALID_TYPES = ['Concept', 'Material', 'Process', 'Organization', 'Person', 'Location', 'Technology', 'Other']

async def classify_nodes():
    conn = sqlite3.connect("structured_db/structured.db")
    nodes = conn.execute("SELECT id, label FROM kg_nodes WHERE type IS NULL OR type = '' OR type = 'None' OR type = 'default'").fetchall()
    
    if not nodes:
        print("No nodes to classify.")
        return
        
    print(f"Found {len(nodes)} nodes to classify.")
    
    batch_size = 50
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(nodes), batch_size):
            batch = nodes[i:i+batch_size]
            node_labels = [n[1] for n in batch]
            
            prompt = (
                f"Classify the following list of entities into exactly one of these categories: "
                f"{', '.join(VALID_TYPES)}.\n\n"
                "Return a strictly valid JSON object where keys are the entity names and values are the categories.\n"
                "Do not include any explanations or markdown.\n\n"
                f"Entities to classify:\n{json.dumps(node_labels)}"
            )
            
            print(f"Classifying batch {i//batch_size + 1}...")
            
            try:
                resp = await client.post(OLLAMA_URL, json={
                    "model": MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.0}
                })
                
                if resp.status_code == 200:
                    result = resp.json().get("response", "").strip()
                    mapping = json.loads(result)
                    
                    for node_id, label in batch:
                        cat = mapping.get(label)
                        if not cat or cat not in VALID_TYPES:
                            cat = "Other"
                        conn.execute("UPDATE kg_nodes SET type = ? WHERE id = ?", (cat, node_id))
                    conn.commit()
                else:
                    print(f"Failed to get response: {resp.status_code}")
            except Exception as e:
                print(f"Error classifying batch: {e}")
                
    print("Done classifying nodes.")
    
if __name__ == "__main__":
    asyncio.run(classify_nodes())
