import requests

api_endpoint = 'https://api.openai.com/v1/chat/completions'


def generate_link_summary(url, api_key):
    prompt = f"summarize the following url in 100 characters or less: {url}"
    max_summary_length = 25

    response = requests.post(
        api_endpoint,
        headers={'Authorization': f'Bearer {api_key}'},
        json={
            'model': "gpt-3.5-turbo",
            "messages": 
                [
                    {
                        "role": "user",
                        "content": prompt
                    },
                ],
            'max_tokens': max_summary_length
        }
    )

    if response.status_code == 200:
        summary = response.json()['choices'][0]['message']['content']
        return summary
    else:
        # Handle API request errors here
        return None