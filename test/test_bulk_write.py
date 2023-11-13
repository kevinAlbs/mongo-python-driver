# test_bulk_write.py tests existing bulk write behavior. Used for researching WRITING-13533.

# Run tests with:
# python -m pytest ./test/test_bulk_write.py

# To run one test:
# python -m pytest ./test/test_bulk_write.py -k test_can_return_multiple_WCE

# To print stdout:
# python -m pytest ./test/test_bulk_write.py -k test_can_return_multiple_WCE --capture=no

from pymongo import MongoClient, monitoring
from pymongo.collection import Collection
from pymongo.operations import InsertOne, UpdateOne, DeleteOne
from pymongo.errors import BulkWriteError, OperationFailure

import unittest

class test_bulk_write_old (unittest.TestCase):
    def assertCollectionEqual(self, coll : Collection, expected : list):
        got = [doc for doc in coll.find()]
        self.assertListEqual (got, expected)

    def setUp(self) -> None:
        # Drop `db.coll`.
        client = MongoClient()
        coll = client["db"]["coll"]
        coll.drop()
        return super().setUp()
    
    # Test that a bulk write can return multiple writeConcernErrors.
    def test_can_return_multiple_WCE (self):
        # Use a write concern with more nodes than are satisfiable.
        client = MongoClient(w=50)
        coll = client["db"]["coll"]
        with self.assertRaises(BulkWriteError) as ctx:
            coll.bulk_write([InsertOne({'a': 1}), UpdateOne({'a': 1}, {'$set': {'a': 2}})])
        wces = ctx.exception.details['writeConcernErrors']
        self.assertEqual(len(wces), 2)

    # Test an ordered bulk write respects order of models.
    def test_delete_then_insert_ordered (self):
        client = MongoClient()
        coll = client["db"]["coll"]
        coll.bulk_write([
            DeleteOne({'_id': 1}),
            InsertOne({'_id': 1}),
        ])
        # With an ordered bulk write, models are not reordered in driver.
        self.assertCollectionEqual(coll, [{'_id': 1}])

    # Test an unordered bulk write applies insert models before delete models.
    def test_delete_then_insert_unordered (self):
        client = MongoClient()
        coll = client["db"]["coll"]
        coll.bulk_write([
            DeleteOne({'_id': 1}),
            InsertOne({'_id': 1}),
        ], ordered=False)
        # With an unordered bulk write, models are grouped. Pymongo groups inserts before Deletes. This results in the inserted document being deleted.
        self.assertCollectionEqual(coll, [])


    class StoreStarted(monitoring.CommandListener):
        def __init__(self):
            self.started_commands = []

        def started(self, event : monitoring.CommandStartedEvent):
            self.started_commands.append(event.command_name)

        def succeeded(self, _):
            pass

        def failed(self, _):
            pass

    # Test that a command error in an unordered bulk write results in an immediate exception.
    def test_insert_update_unordered_command_failure (self):
        monitor = self.StoreStarted()
        client = MongoClient(appname="test_insert_update_unordered_command_failure", event_listeners=[monitor])
        coll = client["db"]["coll"]
        
        # Set a failpoint on both the "insert" and "update" commands.
        client["admin"].command({
                "configureFailPoint": "failCommand",
                "mode": {"times": 2},
                "data": {
                    "errorCode": 2,
                    "failCommands": ["insert", "update"],
                    # set `appName` to scope the failpoint to this MongoClient.
                    "appName": "test_insert_update_unordered_command_failure"}
        })

        with self.assertRaises(OperationFailure):
            coll.bulk_write([
                InsertOne({'_id': 1}),
                UpdateOne({'_id': 1}, {'$set': { 'a': 1}})
            ], ordered=False)

        # Only the 'insert' command is sent, not the 'update'.
        self.assertListEqual (monitor.started_commands, ["configureFailPoint", "insert"])

if __name__ == "__main__":
    unittest.main()
