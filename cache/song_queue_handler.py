


import json
import os

class SongQueue:
    def __init__(self, file_path="cache/song_queue.json"):
        self.file_path = file_path
        self.data = self.load_data()

    def load_data(self):
        """Load the song queue data from the JSON file."""
        if not os.path.exists(self.file_path):
            return {"servers": {}}
        with open(self.file_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def save_data(self):
        """Save the current song queue data to the JSON file."""
        with open(self.file_path, "w", encoding="utf-8") as file:
            json.dump(self.data, file, indent=4)

    def get_queue(self, server_id):
        """Retrieve the song queue for a specific server."""
        return self.data["servers"].get(server_id, {"current_song": None, "queue": []})

    def add_to_queue(self, server_id, song_url):
        """Add a song to the queue for a specific server."""
        if server_id not in self.data["servers"]:
            self.data["servers"][server_id] = {"current_song": None, "queue": []}
        self.data["servers"][server_id]["queue"].append(song_url)
        self.save_data()

    def get_next_song(self, server_id):
        """Retrieve and remove the next song in the queue for a specific server."""
        if server_id in self.data["servers"] and self.data["servers"][server_id]["queue"]:
            return self.data["servers"][server_id]["queue"].pop(0)
        return None

    def set_current_song(self, server_id, song_url):
        """Set the currently playing song for a specific server."""
        if server_id not in self.data["servers"]:
            self.data["servers"][server_id] = {"current_song": None, "queue": []}
        self.data["servers"][server_id]["current_song"] = song_url
        self.save_data()

    def clear_queue(self, server_id):
        """Clear the song queue for a specific server."""
        if server_id in self.data["servers"]:
            self.data["servers"][server_id]["queue"] = []
            self.save_data()

# Example usage:
# queue = SongQueue()
# queue.add_to_queue("983379337430585344", "https://www.youtube.com/watch?v=example4")
# print(queue.get_queue("983379337430585344"))



