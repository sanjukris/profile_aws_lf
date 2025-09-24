from app.agents.profile_agent import handle_request

def test_email_and_address_flow():
    tool, out = handle_request(
        query="Need my email and postal address", member_id="378477398"
    )
    assert tool == "fetch_email_and_address"
    assert out["header"]["title"].startswith("Your profile for ")
    assert out["data"]["email"][0]["value"] == "SAMPLEEMAILID_1@SAMPLEDOMAIN.COM"

def test_preferences_flow():
    tool, out = handle_request(
        query="Show my contact preferences", member_id="378477398"
    )
    assert tool == "fetch_contact_preference"
    assert out["data"]["preferences"][0]["preferenceUid"] == "HRA"