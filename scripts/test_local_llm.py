import ollama

# List available models correctly
print("Available local models:")
models = ollama.list()
for model in models['models']:
    name = model.get('name', model.get('model', 'unknown'))  # fallback for different versions
    size_gb = model['size'] / (1024 ** 3)
    print(f"- {name} ({size_gb:.1f} GB)")

print("\n" + "="*60)
print("Querying gemma3:1b about context graphs...")
print("="*60)

response = ollama.chat(
    model='gemma3:1b',  # fast and newest — perfect for local dev
    messages=[{
        'role': 'user',
        'content': (
            "In one concise paragraph, explain why 'context graphs' (as described in Foundation Capital's post about AI's trillion-dollar opportunity) "
            "could represent a major breakthrough for building personalized, evolving AI agents. "
            "Focus on decision traces, motives, precedents, and long-term reasoning alignment with the user."
        )
    }]
)

print("\nResponse from gemma3:1b:")
print(response['message']['content'])
