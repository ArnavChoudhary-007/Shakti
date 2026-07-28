import urllib.request, re, json
req = urllib.request.Request('https://ollama.com/search?c=cloud', headers={'User-Agent': 'Mozilla/5.0'})
html = urllib.request.urlopen(req).read().decode()

# Look for model cards
model_sections = re.findall(r'<a href="/library/(.*?)"[^>]*>(.*?)</a>', html, re.DOTALL)
models = []
for m_id, content in model_sections:
    if 'cloud' not in content.lower() and 'cloud' not in m_id.lower():
        continue
    # extract name
    name_match = re.search(r'<span[^>]*>(.*?)</span>', content)
    name = name_match.group(1).strip() if name_match else m_id
    
    # extract description
    desc_match = re.search(r'<p class="max-w-md break-words[^>]*>(.*?)</p>', content, re.DOTALL)
    desc = desc_match.group(1).strip() if desc_match else ""
    
    # extract tags
    tags = re.findall(r'<span class="flex items-center[^>]*>(.*?)</span>', content)
    tags = [re.sub(r'<[^>]+>', '', t).strip() for t in tags]
    
    models.append({
        "name": name,
        "desc": desc,
        "tags": tags
    })

print(json.dumps(models[:10], indent=2))
