import sys
import vertexai
from vertexai.generative_models import GenerativeModel, Part

PROJECT_ID = "cs-poc-hqygfixwzrlhanrfrgx8bx4"
LOCATION = "global"

def main():
    if len(sys.argv) < 2:
        print("Usage: gemini \"your prompt here\"")
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])
    
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    model = GenerativeModel("gemini-3.1-pro-preview")

    try:
        print(f"Calling Gemini with prompt: {prompt}")
        response = model.generate_content(prompt)
        print("-" * 20)
        if response.candidates:
            print(response.text)
        else:
            print("No response candidates were returned by the model.")
            if response.prompt_feedback:
                print(f"Prompt Feedback: {response.prompt_feedback}")
        print("-" * 20)
    except Exception as e:
        print(f"\nError: {e}")
        if hasattr(e, 'details'):
            print(f"Details: {e.details}")

if __name__ == "__main__":
    main()
