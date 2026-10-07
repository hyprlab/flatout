"""The Overview's getting-started list: detected, tickable by hand, hideable."""


def h(csrf):
    return {"X-CSRF": csrf}


def steps(client):
    return {s["id"]: s for s in client.get("/api/v1/setup-checklist").get_json()["steps"]}


def patch(client, csrf, **body):
    return client.patch("/api/v1/setup-checklist", json=body, headers=h(csrf))


def test_steps_are_detected(client, csrf, admin):
    assert not any(s["done"] for s in steps(client).values())
    client.patch("/api/v1/site", json={"theme": {"radius": 8}}, headers=h(csrf))
    theme = steps(client)["theme"]
    assert theme["done"] and theme["auto"] and theme["override"] is None


def test_a_step_can_be_ticked_and_unticked_by_hand(client, csrf, admin):
    data = patch(client, csrf, steps={"key": True}).get_json()
    key = next(s for s in data["steps"] if s["id"] == "key")
    assert key["done"] and key["override"] is True and not key["auto"]

    # Unticking a detected step keeps it unticked.
    client.patch("/api/v1/site", json={"theme": {"radius": 8}}, headers=h(csrf))
    patch(client, csrf, steps={"theme": False})
    assert steps(client)["theme"]["done"] is False

    # Choosing what is detected hands the step back to detection.
    patch(client, csrf, steps={"theme": True})
    assert steps(client)["theme"]["override"] is None
    patch(client, csrf, steps={"key": None})
    assert steps(client)["key"]["done"] is False


def test_all_done_and_hiding(client, csrf, admin):
    data = patch(client, csrf, steps={k: True for k in steps(client)}).get_json()
    assert data["all_done"] is True
    assert "All done." in client.get("/admin").data.decode()

    assert patch(client, csrf, hidden=True).get_json()["hidden"] is True
    page = client.get("/admin").data.decode()
    assert '<section class="panel checklist" id="checklist" hidden>' in page
    assert 'id="checklist-show">' in page   # the way back is on the page
    assert patch(client, csrf, hidden=False).get_json()["hidden"] is False


def test_changes_are_checked(app, client, csrf, admin):
    assert patch(client, csrf, steps={"nope": True}).status_code == 400
    assert patch(client, csrf, steps={"theme": "yes"}).status_code == 400
    assert patch(client, csrf, hidden="yes").status_code == 400
    assert patch(client, csrf, colour="red").status_code == 400
    reader = client.post("/api/v1/tokens", json={"name": "r"}, headers=h(csrf)).get_json()["token"]
    robot = app.test_client()
    assert robot.get("/api/v1/setup-checklist", headers={"Authorization": f"Bearer {reader}"}).status_code == 200
    assert robot.patch("/api/v1/setup-checklist", json={"hidden": True},
                       headers={"Authorization": f"Bearer {reader}"}).status_code == 403
