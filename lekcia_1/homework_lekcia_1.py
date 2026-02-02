"""
Python skript demonštrujúci volanie LLM API s použitím nástrojov (tools/function calling).
Skript volá LLM, LLM rozhodne o použití nástroja, skript vykoná nástroj a vráti výsledok späť LLM.
"""

import json
import os
from openai import OpenAI

# Inicializácia OpenAI klienta
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# Definícia výpočetných funkcií (nástrojov)
def calculator(operation: str, a: float, b: float) -> float:
    """
    Kalkulačka podporujúca základné operácie.
    
    Args:
        operation: Typ operácie (add, subtract, multiply, divide)
        a: Prvé číslo
        b: Druhé číslo
    
    Returns:
        Výsledok operácie
    """
    if operation == "add":
        return a + b
    elif operation == "subtract":
        return a - b
    elif operation == "multiply":
        return a * b
    elif operation == "divide":
        if b == 0:
            return "Error: Division by zero"
        return a / b
    else:
        return "Error: Unknown operation"


def get_current_weather(location: str) -> str:
    """
    Simulovaná funkcia na získanie počasia (v reálnej aplikácii by volala weather API).
    
    Args:
        location: Názov miesta
    
    Returns:
        Informácia o počasí
    """
    # Simulovaná odpoveď
    weather_data = {
        "Prague": "20°C, Slnečno",
        "Bratislava": "22°C, Oblačno",
        "London": "15°C, Dážď"
    }
    return weather_data.get(location, "Počasie pre toto miesto nie je dostupné")


# Definícia nástrojov pre LLM (OpenAI function calling format)
tools = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Vykonáva základné matematické operácie",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "Typ operácie"
                    },
                    "a": {
                        "type": "number",
                        "description": "Prvé číslo"
                    },
                    "b": {
                        "type": "number",
                        "description": "Druhé číslo"
                    }
                },
                "required": ["operation", "a", "b"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "Získa aktuálne počasie pre dané miesto",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Názov mesta alebo miesta"
                    }
                },
                "required": ["location"]
            }
        }
    }
]


# Mapovanie názvov funkcií na skutočné funkcie
available_functions = {
    "calculator": calculator,
    "get_current_weather": get_current_weather
}


def run_conversation(user_query: str):
    """
    Hlavná funkcia - vykonáva konverzáciu s LLM vrátane použitia nástrojov.
    
    Args:
        user_query: Otázka alebo príkaz od používateľa
    """
    print(f"\n{'='*60}")
    print(f"Používateľská otázka: {user_query}")
    print(f"{'='*60}\n")
    
    # Krok 1: Prvé volanie LLM s používateľskou otázkou
    messages = [{"role": "user", "content": user_query}]
    
    print("1️⃣ Volám LLM API s otázkou...\n")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=tools,
        tool_choice="auto"  # LLM sa samo rozhodne, či použiť nástroj
    )
    
    response_message = response.choices[0].message
    tool_calls = response_message.tool_calls
    
    # Krok 2: Kontrola, či LLM chce použiť nástroj
    if tool_calls:
        print(f"2️ LLM sa rozhodol použiť nástroj(e):\n")
        
        # Pridanie odpovede LLM do histórie
        messages.append(response_message)
        
        # Krok 3: Vykonanie každého nástroja, ktorý LLM požaduje
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            
            print(f"   Nástroj: {function_name}")
            print(f"   Argumenty: {function_args}")
            
            # Vykonanie funkcie
            function_to_call = available_functions[function_name]
            function_response = function_to_call(**function_args)
            
            print(f"   Výsledok: {function_response}\n")
            
            # Krok 4: Pridanie výsledku nástroja do správ pre LLM
            messages.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": str(function_response)
            })
        
        # Krok 5: Druhé volanie LLM s výsledkami nástrojov
        print("3️⃣ Volám LLM znovu s výsledkami nástrojov...\n")
        second_response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages
        )
        
        final_answer = second_response.choices[0].message.content
        print(f"4️⃣ Finálna odpoveď LLM:\n")
        print(f"   {final_answer}\n")
        
    else:
        # LLM nepotrebuje nástroj, odpoveď priamo
        print(f"2️⃣ LLM odpovedal bez použitia nástroja:\n")
        print(f"   {response_message.content}\n")
    
    print(f"{'='*60}\n")


# Hlavný program
if __name__ == "__main__":
    # Príklady otázok, ktoré využijú rôzne nástroje
    
    print("\n" + "🤖 DEMO: LLM API s Function Calling".center(60))
    
    # Príklad 1: Matematická operácia
    run_conversation("Koľko je 16 krát 16?")
    
    # Príklad 2: Počasie
    run_conversation("Aké je počasie v Bratislave?")
    
    # Príklad 3: Kombinovaná otázka
    run_conversation("Vypočítaj 150 deleno 3 a potom mi povedz počasie v Prahe")
    
    # Príklad 4: Bez použitia nástroja
    run_conversation("Kto bol prvý človek na Mesiaci?")
