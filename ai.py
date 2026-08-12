import os, json
from google import genai
from dotenv import load_dotenv
client=genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
load_dotenv()
def generate_fitness_plan(profile):
    prompt=f"""You are an AI fitness planner for a fitness tracking application.
    Create a safe and practical fitness plan based on the user's profile.
    USER PROFILE:
    {json.dumps(profile, indent=2)}
    Rules:
    - Respect the user's fitness level.
    - Respect the number of workout days.
    - Only use the equipment available to the user.
    - Give realistic exercises.
    - Include sets, repetitions and rest time.
    - Include warm-up and cooldown.
    - Give basic nutrition guidance.
    - Do not recommend dangerous or extreme methods.
    - Return ONLY valid JSON.
    """
    response=client.models.generate_content(model="gemini-3.1-flash-lite", contents=prompt, 
                                            config={"response_mime_type": "application/json"})
    ai_result=json.loads(response.text)
    return json.dumps(ai_result)