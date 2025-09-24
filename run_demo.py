from app.agents.profile_agent import handle_request
import dotenv
dotenv.load_dotenv()

if __name__ == "__main__":
    t1, o1 = handle_request(
        query="What is my email and postal address?", member_id="378477398"
    )
    print("intent:", t1)
    print(o1)

    t2, o2 = handle_request(
        query="Show my contact preferences", member_id="378477398"
    )
    print("intent:", t2)
    print(o2)