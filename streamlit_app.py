import streamlit as st
from supabase import create_client
from google import genai
from google.genai import types
import datetime

# --- 1. CONFIGURATION ---
# Change this in your app:
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = genai.Client(api_key=GEMINI_API_KEY)

st.set_page_config(page_title="GarageMind AI", layout="centered", page_icon="📦")
st.title("📦 GarageMind: Organizer")

tab1, tab2, tab3, tab4 = st.tabs(["Add Item", "Find/Manage", "Setup Racks", "Dashboard"])

# --- TAB 1: ADDING ---
with tab1:
    st.header("Scan an Object")
    
    # FETCH LIVE LOCATIONS FROM SUPABASE
    try:
        loc_res = supabase.table("locations").select("id").execute()
        bin_options = [row['id'] for row in loc_res.data] if loc_res.data else ["R1B1"]
    except:
        bin_options = ["Register a rack in Tab 3 first"]

    img_file = st.camera_input("Take a photo")

    if img_file:
        img_bytes = img_file.getvalue()
        
        if "last_img" not in st.session_state or st.session_state.last_img != img_bytes:
            with st.spinner("AI is identifying..."):
                image_part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                
                response = client.models.generate_content(
                    model='gemini-3.1-flash-lite-preview',
                    contents=[
                        "Identify this. Return: Name | Category",
                        image_part
                    ]
                )
                
                try:
                    name_g, cat_g = [x.strip() for x in response.text.split("|")]
                except:
                    name_g, cat_g = response.text.split("\n")[0], "Misc"
                
                st.session_state.item_name = name_g
                st.session_state.item_cat = cat_g
                st.session_state.last_img = img_bytes

        st.divider()
        final_name = st.text_input("Item Name", value=st.session_state.get('item_name', ''))
        final_cat = st.text_input("Description", value=st.session_state.get('item_cat', ''))
        final_bin = st.selectbox("Storage Location (Rack/Bin)", options=bin_options)
        count = st.number_input("Quantity", min_value=1, value=1)

        if st.button("Confirm & Save"):
            with st.spinner("Saving..."):
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"{ts}_{final_name.replace(' ', '_')}.jpg"
                supabase.storage.from_("object-photos").upload(path=file_name, file=img_bytes)

                img_url_res = supabase.storage.from_("object-photos").get_public_url(file_name)

                # If it's a string, use it directly; if it's an object, grab the attribute
                if isinstance(img_url_res, str):
                    img_url = img_url_res
                else:
                    img_url = img_url_res.public_url

                emb_res = client.models.embed_content(
                    model='gemini-embedding-001', 
                    contents=[f"{final_name} {final_cat}"]
                )
                vector = emb_res.embeddings[0].values

                supabase.table("inventory").insert({
                    "name": final_name, 
                    "description": final_cat,
                    "location_id": final_bin, 
                    "image_url": img_url, 
                    "embedding": vector,
                    "quantity": count
                }).execute()
                st.success(f"Saved {final_name} to {final_bin}!")
                st.balloons()

# --- TAB 2: SEARCH ---
with tab2:
    st.header("🔍 Search Inventory")
    query = st.text_input("Search your garage...", key="garage_search_input")
    
    if query:
        q_emb = client.models.embed_content(model='gemini-embedding-001', contents=[query])
        q_vector = q_emb.embeddings[0].values

        rpc_res = supabase.rpc("match_inventory", {
            "query_embedding": q_vector, "match_threshold": 0.5, "match_count": 5
        }).execute()

        if not rpc_res.data:
            st.warning("🔍 Not found! I don't have anything like that in the garage yet.")
        else:
            for item in rpc_res.data:
                with st.container(border=True):
                    col1, col2 = st.columns([1, 2])
                    with col1:
                        st.image(item['image_url'], use_container_width=True)
                    with col2:
                        st.subheader(item['name'])
                        st.info(f"📍 **Location:** {item['location_id']}")
                        st.write(f"**Quantity:** {item.get('quantity', 1)}")
                        
                        if st.button(f"🗑️ Delete {item['name']}", key=f"del_{item['id']}"):
                            supabase.table("inventory").delete().eq("id", item['id']).execute()
                            st.rerun()

# --- TAB 3: SETUP & EDIT ---
with tab3:
    st.header("🏗️ Manage Racks & Bins")
    
    # 1. Fetch existing locations for the editor
    loc_res = supabase.table("locations").select("*").order("id").execute()
    existing_locs = {row['id']: row for row in loc_res.data} if loc_res.data else {}

    menu = st.radio("Action", ["Add New", "Edit Existing"], horizontal=True)

    if menu == "Add New":
        with st.form("new_location"):
            loc_id = st.text_input("Location ID (e.g., R1B1)")
            loc_tag = st.text_input("Friendly Name (e.g., Top Shelf)")
            loc_type = st.selectbox("Type", ["bin", "shelf", "workbench", "room"])
            if st.form_submit_button("Register"):
                supabase.table("locations").insert({
                    "id": loc_id, "type": loc_type, "category_tag": loc_tag, "is_full": False
                }).execute()
                st.success(f"Registered {loc_id}!")
                st.rerun()

    else:
        if not existing_locs:
            st.info("No racks to edit yet.")
        else:
            target_id = st.selectbox("Select Rack to Edit", options=list(existing_locs.keys()))
            current_data = existing_locs[target_id]
            
            with st.form("edit_location"):
                st.write(f"Editing: **{target_id}**")
                new_tag = st.text_input("New Friendly Name", value=current_data['category_tag'])
                new_type = st.selectbox("New Type", ["bin", "shelf", "workbench", "room"], 
                                        index=["bin", "shelf", "workbench", "room"].index(current_data['type']))
                
                if st.form_submit_button("Update Location"):
                    supabase.table("locations").update({
                        "category_tag": new_tag, 
                        "type": new_type
                    }).eq("id", target_id).execute()
                    st.success(f"Updated {target_id}!")
                    st.rerun()
# --- TAB 4: WAREHOUSE VIEW ---
with tab4:
    st.header("📊 Full Inventory by Location")
    
    # 1. Pull all registered locations
    all_locs = supabase.table("locations").select("*").order("id").execute()
    
    if not all_locs.data:
        st.info("No racks registered. Go to 'Setup Racks' to start.")
    else:
        for loc in all_locs.data:
            # Setting expanded=True makes all bin contents visible on page load
            with st.expander(f"📍 {loc['id']} — {loc['category_tag']}", expanded=True):
                # 2. Fetch items for this specific ID
                items = supabase.table("inventory").select("*").eq("location_id", loc['id']).execute()
                
                if not items.data:
                    st.caption("Empty bin.")
                else:
                    for item in items.data:
                        c1, c2 = st.columns([1, 4])
                        with c1:
                            st.image(item['image_url'], width=70)
                        with c2:
                            st.write(f"**{item['name']}**")
                            st.write(f"Qty: {item.get('quantity', 1)}")
