-- Runs once, when the db container starts with an empty volume.
-- Creates a second database for the test suite so tests never touch dev data.
CREATE DATABASE ledgerly_test;
