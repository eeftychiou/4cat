"""
Class that represents a job in the job queue
"""

import time
import json
import math
from common.lib.exceptions import JobClaimedException, JobNotFoundException
from common.lib.helpers import get_instance_id


class Job:
	"""
	Job in queue
	"""
	data = {}
	db = None

	is_finished = False
	is_claimed = False

	def __init__(self, data, database=None):
		"""
		Instantiate Job object

		:param dict data:  Job data, should correspond to a database record
		:param database:  Database handler
		"""
		self.data = data
		self.db = database

		self.data["remote_id"] = str(self.data["remote_id"])

		self.is_finished = "is_finished" in self.data and self.data["is_finished"]
		self.is_claimed = self.data["timestamp_claimed"] and self.data["timestamp_claimed"] > 0

	@staticmethod
	def get_by_ID(job_id, database, own_instance_only=True):
		"""
		Instantiate job object by ID

		:param int job_id: Job ID
		:param database:  Database handler
		:param bool own_instance_only:  Only consider jobs that are claimable
		or claimed by this instance. If `False`, also considers jobs currently
		claimed by other instances.
		:return Job: Job object
		"""
		instance_filter = ("*", get_instance_id()) if own_instance_only else ()
		if instance_filter:
			data = database.fetchone("SELECT * FROM jobs WHERE id = %s AND instance IN %s", (job_id, instance_filter))
		else:
			data = database.fetchone("SELECT * FROM jobs WHERE id = %s", (job_id,))

		if not data:
			raise JobNotFoundException

		return Job.get_by_data(data, database)

	@staticmethod
	def get_by_data(data, database):
		"""
		Instantiate job object with given data

		:param dict data:  Job data, should correspond to a database row
		:param database: Database handler
		:return Job: Job object
		"""
		return Job(data, database)

	@staticmethod
	def get_by_remote_ID(remote_id, database, jobtype="*", own_instance_only=True):
		"""
		Instantiate job object by combination of remote ID and job type

		This combination is guaranteed to be unique.

		:param database: Database handler
		:param str jobtype: Job type
		:param str remote_id: Job remote ID
		:param bool own_instance_only:  Only consider jobs that are claimable
		or claimed by this instance. If `False`, also considers jobs currently
		claimed by other instances.
		:return Job: Job object
		"""
		instance_filter = ("*", get_instance_id()) if own_instance_only else ()

		if jobtype != "*":
			if instance_filter:
				data = database.fetchone("SELECT * FROM jobs WHERE jobtype = %s AND remote_id = %s AND instance IN %s",
										 (jobtype, remote_id, instance_filter))
			else:
				data = database.fetchone("SELECT * FROM jobs WHERE jobtype = %s AND remote_id = %s",
										 (jobtype, remote_id))
		else:
			if instance_filter:
				data = database.fetchone("SELECT * FROM jobs WHERE remote_id = %s AND instance IN %s", (remote_id, instance_filter))
			else:
				data = database.fetchone("SELECT * FROM jobs WHERE remote_id = %s", (remote_id,))

		if not data:
			raise JobNotFoundException

		return Job.get_by_data(data, database=database)

	def claim(self):
		"""
		Claim a job

		This marks it in the database so it cannot be claimed again. If the job
		is set up to be executed by any 4CAT instance, it is linked to this
		specific instance after claiming.
		"""
		if self.data["interval"] == 0:
			claim_time = int(time.time())
		else:
			# the claim time should be a multiple of the interval to prevent
			# drift of the interval over time. this ensures that on average,
			# the interval remains as set
			claim_time = math.floor(int(time.time()) / self.data["interval"]) * self.data["interval"]

		updated = self.db.update("jobs", data={"timestamp_claimed": claim_time, "timestamp_lastclaimed": claim_time, "instance": get_instance_id()},
								 where={"id": self.data["id"], "timestamp_claimed": 0})

		if updated == 0:
			raise JobClaimedException

		self.data["timestamp_claimed"] = claim_time
		self.data["timestamp_lastclaimed"] = claim_time

		self.is_claimed = True

	def finish(self, delete=False):
		"""
		Finish job

		This deletes it from the database, or in the case of recurring jobs,
		resets the claim flags.

		:param bool delete: Whether to force deleting the job even if it is a
							job with an interval.
		"""
		if self.data["interval"] == 0 or delete:
			self.db.delete("jobs", where={"id": self.data["id"]})
		else:
			self.db.update("jobs", data={"timestamp_claimed": 0, "attempts": 0}, where={"id": self.data["id"]})

		self.is_finished = True

	def release(self, delay=0, claim_after=0):
		"""
		Release a job so it may be claimed again

		:param int delay: Delay in seconds after which job may be reclaimed.
		:param int claim_after:  Timestamp after which job may be claimed. This
		is overridden by `delay`.
		"""
		update = {"timestamp_claimed": 0, "attempts": self.data["attempts"] + 1}
		if delay > 0:
			update["timestamp_after"] = int(time.time()) + delay
		elif claim_after is not None:
			update["timestamp_after"] = claim_after

		self.db.update("jobs", data=update, where={"id": self.data["id"]})
		self.is_claimed = False

	def is_claimable(self):
		"""
		Can this job be claimed?

		:return bool: If the job is not claimed yet and also isn't finished.
		"""
		return \
			not self.is_claimed \
			and not self.is_finished \
			and self.data["timestamp_lastclaimed"] < time.time() - self.data["interval"]

	@property
	def details(self):
		try:
			details = json.loads(self.data["details"])
			if details:
				return details
			else:
				return {}
		except (TypeError, json.JSONDecodeError):
			return {}
