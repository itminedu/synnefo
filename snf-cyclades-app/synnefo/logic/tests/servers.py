# vim: set fileencoding=utf-8 :
# Copyright (C) 2010-2017 GRNET S.A. and individual contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Provides automated tests for logic module
from django.test import TransactionTestCase
from synnefo.logic import servers
from synnefo.logic import backend
from synnefo.logic.backend import GNT_EXTP_VOLTYPESPEC_PREFIX
from synnefo import quotas
from synnefo.db import models_factory as mfactory, models
from synnefo.db import transaction
from mock import patch, Mock

from snf_django.lib.api import faults, Credentials
from snf_django.utils.testing import mocked_quotaholder, override_settings
from django.conf import settings
from copy import deepcopy

fixed_image = Mock()
fixed_image.return_value = {'location': 'pithos://foo',
                            'mapfile': 'test_mapfile',
                            "id": 1,
                            "name": "test_image",
                            "version": 42,
                            "is_public": True,
                            "owner": "user2",
                            "status": "AVAILABLE",
                            "size": 1000,
                            "is_snapshot": False,
                            'disk_format': 'diskdump'}


@patch('synnefo.api.util.get_image', fixed_image)
@patch("synnefo.logic.rapi_pool.GanetiRapiClient")
class ServerCreationTest(TransactionTestCase):
    def setUp(self):
        self.credentials = Credentials("test")

    def test_create(self, mrapi):
        flavor = mfactory.FlavorFactory()
        kwargs = {
            "credentials": self.credentials,
            "name": "test_vm",
            "password": "1234",
            "flavor": flavor,
            "image_id": "safs",
            "networks": [],
            "metadata": {"foo": "bar"},
            "personality": [],
        }
        # no backend!
        mfactory.BackendFactory(offline=True)
        self.assertRaises(faults.ServiceUnavailable, servers.create, **kwargs)
        self.assertEqual(models.VirtualMachine.objects.count(), 0)

        mfactory.IPv4SubnetFactory(network__public=True)
        mfactory.IPv6SubnetFactory(network__public=True)
        backend = mfactory.BackendFactory()

        # error in nics
        req = deepcopy(kwargs)
        req["networks"] = [{"uuid": 42}]
        self.assertRaises(faults.ItemNotFound, servers.create, **req)
        self.assertEqual(models.VirtualMachine.objects.count(), 0)

        # error in enqueue. check the vm is deleted and resources released
        mrapi().CreateInstance.side_effect = Exception("ganeti is down")
        with mocked_quotaholder():
            servers.create(**kwargs)
        vm = models.VirtualMachine.objects.get()
        self.assertFalse(vm.deleted)
        self.assertEqual(vm.operstate, "ERROR")
        for nic in vm.nics.all():
            self.assertEqual(nic.state, "ERROR")

        # test ext settings:
        req = deepcopy(kwargs)
        vlmt = mfactory.VolumeTypeFactory(disk_template='ext_archipelago')
        # Generate 4 specs. 2 prefixed with GNT_EXTP_VOLTYPESPEC_PREFIX
        # and 2 with an other prefix that should be omitted
        volume_type_specs = [
            mfactory.VolumeTypeSpecsFactory(
                volume_type=vlmt, key='%sbar' % GNT_EXTP_VOLTYPESPEC_PREFIX),
            mfactory.VolumeTypeSpecsFactory(
                volume_type=vlmt, key='%sfoo' % GNT_EXTP_VOLTYPESPEC_PREFIX),
            mfactory.VolumeTypeSpecsFactory(
                volume_type=vlmt, key='other-prefx-baz'),
            mfactory.VolumeTypeSpecsFactory(
                volume_type=vlmt, key='another-prefix-biz'),
        ]

        gnt_prefixed_specs = filter(lambda s: s.key.startswith(
            GNT_EXTP_VOLTYPESPEC_PREFIX), volume_type_specs)
        ext_flavor = mfactory.FlavorFactory(
            volume_type=vlmt,
            disk=1)
        req["flavor"] = ext_flavor
        mrapi().CreateInstance.return_value = 42
        backend.disk_templates = ["ext"]
        backend.save()
        osettings = {
            "GANETI_DISK_PROVIDER_KWARGS": {
                "archipelago": {
                    "foo": "mpaz",
                    "lala": "lolo"
                }
            }
        }
        with mocked_quotaholder():
            with override_settings(settings, **osettings):
                with patch(
                    'synnefo.logic.backend_allocator.update_backends_disk_templates'  # noqa E265
                ) as update_disk_templates_mock:
                    # Check that between the `get_available_backends` call
                    # and the `update_backend_disk_templates` call
                    # the backend doesn't change.
                    update_disk_templates_mock.return_value = [backend]
                    vm = servers.create(**req)

        update_disk_templates_mock.assert_called_once_with([backend])
        name, args, kwargs = mrapi().CreateInstance.mock_calls[-1]
        disk_kwargs = {"provider": "archipelago",
                       "origin": "test_mapfile",
                       "origin_size": 1000,
                       "name": vm.volumes.all()[0].backend_volume_uuid,
                       "foo": "mpaz",
                       "lala": "lolo",
                       "size": 1024}
        disk_kwargs.update({spec.key[len(GNT_EXTP_VOLTYPESPEC_PREFIX):]:
                            spec.value
                            for spec in gnt_prefixed_specs})
        self.assertEqual(kwargs["disks"][0], disk_kwargs)


@patch("synnefo.logic.rapi_pool.GanetiRapiClient")
class ServerTest(TransactionTestCase):

    def test_connect_network(self, mrapi):
        # Common connect
        for dhcp in [True, False]:
            subnet = mfactory.IPv4SubnetFactory(network__flavor="CUSTOM",
                                                cidr="192.168.2.0/24",
                                                gateway="192.168.2.1",
                                                dhcp=dhcp)
            net = subnet.network
            vm = mfactory.VirtualMachineFactory(operstate="STARTED")
            mfactory.BackendNetworkFactory(network=net, backend=vm.backend)
            mrapi().ModifyInstance.return_value = 42
            with override_settings(settings, GANETI_USE_HOTPLUG=True):
                with transaction.atomic():
                    port = servers._create_port(vm.userid, net)
                    servers.connect_port(vm, net, port)
            pool = net.get_ip_pools(locked=False)[0]
            self.assertFalse(pool.is_available("192.168.2.2"))
            args, kwargs = mrapi().ModifyInstance.call_args
            nics = kwargs["nics"][0]
            self.assertEqual(kwargs["instance"], vm.backend_vm_id)
            self.assertEqual(nics[0], "add")
            self.assertEqual(nics[1], "-1")
            self.assertEqual(nics[2]["ip"], "192.168.2.2")
            self.assertEqual(nics[2]["network"], net.backend_id)

        # Test connect to IPv6 only network
        vm = mfactory.VirtualMachineFactory(operstate="STARTED")
        subnet = mfactory.IPv6SubnetFactory(cidr="2000::/64",
                                            gateway="2000::1")
        net = subnet.network
        mfactory.BackendNetworkFactory(network=net, backend=vm.backend)
        with override_settings(settings, GANETI_USE_HOTPLUG=True):
            with transaction.atomic():
                port = servers._create_port(vm.userid, net)
                servers.connect_port(vm, net, port)
        args, kwargs = mrapi().ModifyInstance.call_args
        nics = kwargs["nics"][0]
        self.assertEqual(kwargs["instance"], vm.backend_vm_id)
        self.assertEqual(nics[0], "add")
        self.assertEqual(nics[1], "-1")
        self.assertEqual(nics[2]["ip"], None)
        self.assertEqual(nics[2]["network"], net.backend_id)

    def test_attach_volume_type_specs(self, mrapi):
        """Test volume type spces propagation when attaching a
           volume to an instance
        """
        vlmt = mfactory.VolumeTypeFactory(disk_template='ext_archipelago')
        # Generate 4 specs. 2 prefixed with GNT_EXTP_VOLTYPESPEC_PREFIX
        # and 2 with an other prefix that should be omitted
        volume_type_specs = [
            mfactory.VolumeTypeSpecsFactory(
                volume_type=vlmt, key='%sbar' % GNT_EXTP_VOLTYPESPEC_PREFIX),
            mfactory.VolumeTypeSpecsFactory(
                volume_type=vlmt, key='%sfoo' % GNT_EXTP_VOLTYPESPEC_PREFIX),
            mfactory.VolumeTypeSpecsFactory(
                volume_type=vlmt, key='other-prefx-baz'),
            mfactory.VolumeTypeSpecsFactory(
                volume_type=vlmt, key='another-prefix-biz'),
        ]

        gnt_prefixed_specs = filter(lambda s: s.key.startswith(
            GNT_EXTP_VOLTYPESPEC_PREFIX), volume_type_specs)
        volume = mfactory.VolumeFactory(volume_type=vlmt, size=1)
        vm = volume.machine
        osettings = {
            "GANETI_DISK_PROVIDER_KWARGS": {
                "archipelago": {
                    "foo": "mpaz",
                    "lala": "lolo"
                }
            }
        }

        with override_settings(settings, **osettings):
            mrapi().ModifyInstance.return_value = 1
            jobid = backend.attach_volume(vm, volume)
            self.assertEqual(jobid, 1)
            name, args, kwargs = mrapi().ModifyInstance.mock_calls[-1]

            disk_kwargs = {"provider": "archipelago",
                           "name": vm.volumes.all()[0].backend_volume_uuid,
                           "reuse_data": 'False',
                           "foo": "mpaz",
                           "lala": "lolo",
                           "size": 1024}
            disk_kwargs.update({spec.key[len(GNT_EXTP_VOLTYPESPEC_PREFIX):]:
                                spec.value
                                for spec in gnt_prefixed_specs})

        # Should be "disks": [('add', '-1', {disk_kwargs}), ]
        disk = kwargs["disks"][0]
        self.assertEqual(disk[0], 'add')
        self.assertEqual(disk[1], '-1')
        self.assertEqual(disk[2], disk_kwargs)

    def test_attach_wait_for_sync(self, mrapi):
        """Test wait_for_sync when attaching volume to instance.

        """
        volume = mfactory.VolumeFactory()
        vm = volume.machine
        # Test Started VM
        vm.operstate = "STARTED"
        vm.save()
        mrapi().ModifyInstance.return_value = 1
        for sync in [True, False]:
            with override_settings(settings, GANETI_DISKS_WAIT_FOR_SYNC=sync):
                jobid = backend.attach_volume(vm, volume)
                self.assertEqual(jobid, 1)
                name, args, kwargs = mrapi().ModifyInstance.mock_calls[-1]
                self.assertEqual(kwargs['wait_for_sync'], sync)

        # Test Stopped VM. We do not pass wait_for_sync.
        vm.operstate = "STOPPED"
        vm.save()
        mrapi().ModifyInstance.return_value = 1
        for sync in [True, False]:
            with override_settings(settings, GANETI_DISKS_WAIT_FOR_SYNC=sync):
                jobid = backend.attach_volume(vm, volume)
                self.assertEqual(jobid, 1)
                name, args, kwargs = mrapi().ModifyInstance.mock_calls[-1]
                self.assertFalse('wait_for_sync' in kwargs)


@patch("synnefo.logic.rapi_pool.GanetiRapiClient")
class ServerCommandTest(TransactionTestCase):
    def setUp(self):
        self.credentials = Credentials("admin_id", is_admin=True)

    def test_pending_task(self, mrapi):
        vm = mfactory.VirtualMachineFactory(task="REBOOT", task_job_id=1)
        self.assertRaises(faults.BadRequest, servers.start, vm.id,
                          credentials=self.credentials)
        vm = mfactory.VirtualMachineFactory(task="BUILD", task_job_id=1)
        self.assertRaises(faults.BuildInProgress, servers.start, vm.id,
                          credentials=self.credentials)
        # Assert always succeeds
        vm = mfactory.VirtualMachineFactory(task="BUILD", task_job_id=1)
        mrapi().DeleteInstance.return_value = 1
        with mocked_quotaholder():
            servers.destroy(vm.id, credentials=self.credentials)
        vm = mfactory.VirtualMachineFactory(task="REBOOT", task_job_id=1)
        with mocked_quotaholder():
            servers.destroy(vm.id, credentials=self.credentials)

    def test_deleted_vm(self, mrapi):
        vm = mfactory.VirtualMachineFactory(deleted=True)
        self.assertRaises(faults.BadRequest, servers.start, vm.id,
                          self.credentials)

    def test_invalid_operstate_for_action(self, mrapi):
        vm = mfactory.VirtualMachineFactory(operstate="STARTED")
        self.assertRaises(faults.BadRequest, servers.start, vm.id,
                          credentials=self.credentials)
        vm = mfactory.VirtualMachineFactory(operstate="STOPPED")
        self.assertRaises(faults.BadRequest, servers.stop, vm.id,
                          credentials=self.credentials)
        vm = mfactory.VirtualMachineFactory(operstate="STARTED")
        flavor = mfactory.FlavorFactory()
        self.assertRaises(faults.BadRequest, servers.resize, vm.id, flavor,
                          credentials=self.credentials)
        # Check that connect/disconnect is allowed only in STOPPED vms
        # if hotplug is disabled.
        vm = mfactory.VirtualMachineFactory(operstate="STARTED")
        network = mfactory.NetworkFactory(state="ACTIVE")
        with override_settings(settings, GANETI_USE_HOTPLUG=False):
            port = servers._create_port(vm.userid, network)
            self.assertRaises(
                faults.BadRequest, servers.connect_port, vm, network, port)
            self.assertRaises(faults.BadRequest, servers.disconnect_port,
                              vm, network)
        # test valid
        vm = mfactory.VirtualMachineFactory(operstate="STOPPED")
        mrapi().StartupInstance.return_value = 1
        with mocked_quotaholder():
            servers.start(vm.id, credentials=self.credentials)
        vm = models.VirtualMachine.objects.get(id=vm.id)
        vm.task = None
        vm.task_job_id = None
        vm.save()
        with mocked_quotaholder():
            quotas.accept_resource_serial(vm)
        mrapi().RebootInstance.return_value = 1
        with mocked_quotaholder():
            servers.reboot(vm.id, "HARD", credentials=self.credentials)

    def test_commission(self, mrapi):
        vm = mfactory.VirtualMachineFactory(operstate="STOPPED")
        # Still pending
        vm.serial = mfactory.QuotaHolderSerialFactory(serial=200,
                                                      resolved=False,
                                                      pending=True)
        vm.save()
        serial = vm.serial
        mrapi().StartupInstance.return_value = 1
        with mocked_quotaholder() as m:
            with self.assertRaises(quotas.ResolveError):
                servers.start(vm.id, credentials=self.credentials)
        # Not pending, rejct
        vm.task = None
        vm.serial = mfactory.QuotaHolderSerialFactory(serial=400,
                                                      resolved=False,
                                                      pending=False,
                                                      accept=False)
        vm.save()
        serial = vm.serial
        mrapi().StartupInstance.return_value = 1
        with mocked_quotaholder() as m:
            servers.start(vm.id, credentials=self.credentials)
            m.resolve_commissions.assert_called_once_with([],
                                                          [serial.serial])
            self.assertTrue(m.issue_one_commission.called)
        # Not pending, accept
        vm.task = None
        vm.serial = mfactory.QuotaHolderSerialFactory(serial=600,
                                                      resolved=False,
                                                      pending=False,
                                                      accept=True)
        vm.save()
        serial = vm.serial
        mrapi().StartupInstance.return_value = 1
        with mocked_quotaholder() as m:
            servers.start(vm.id, credentials=self.credentials)
            m.resolve_commissions.assert_called_once_with([serial.serial],
                                                          [])
            self.assertTrue(m.issue_one_commission.called)

        mrapi().StartupInstance.side_effect = ValueError
        vm.task = None
        vm.serial = None
        vm.save()
        # Test reject if Ganeti erro
        with mocked_quotaholder() as m:
            try:
                servers.start(vm.id, credentials=self.credentials)
            except Exception:
                (accept, reject), kwargs = m.resolve_commissions.call_args
                self.assertEqual(accept, [])
                self.assertEqual(len(reject), 1)
                self.assertEqual(kwargs, {})
            else:
                raise AssertionError("Starting a server should raise an"
                                     " exception.")

    def test_task_after(self, mrapi):
        return
        vm = mfactory.VirtualMachineFactory()
        mrapi().StartupInstance.return_value = 1
        mrapi().ShutdownInstance.return_value = 2
        mrapi().RebootInstance.return_value = 2
        with mocked_quotaholder():
            vm.task = None
            vm.operstate = "STOPPED"
            vm.save()
            servers.start(vm.id, credentials=self.credentials)
            self.assertEqual(vm.task, "START")
            self.assertEqual(vm.task_job_id, 1)
        with mocked_quotaholder():
            vm.task = None
            vm.operstate = "STARTED"
            vm.save()
            servers.stop(vm.id, credentials=self.credentials)
            self.assertEqual(vm.task, "STOP")
            self.assertEqual(vm.task_job_id, 2)
        with mocked_quotaholder():
            vm.task = None
            vm.save()
            servers.reboot(vm.id, credentials=self.credentials)
            self.assertEqual(vm.task, "REBOOT")
            self.assertEqual(vm.task_job_id, 3)

    def test_reassign_vm(self, mrapi):
        volume = mfactory.VolumeFactory()
        vm = volume.machine
        another_project = "another_project"
        with mocked_quotaholder():
            vm = servers.reassign(vm.id, another_project, False,
                                  credentials=self.credentials)
            self.assertEqual(vm.project, another_project)
            self.assertEqual(vm.shared_to_project, False)
            vol = vm.volumes.get(id=volume.id)
            self.assertNotEqual(vol.project, another_project)

        volume = mfactory.VolumeFactory()
        volume.index = 0
        volume.save()
        vm = volume.machine
        another_project = "another_project"
        with mocked_quotaholder():
            vm = servers.reassign(vm.id, another_project, True,
                                  credentials=self.credentials)
            self.assertEqual(vm.project, another_project)
            self.assertEqual(vm.shared_to_project, True)
            vol = vm.volumes.get(id=volume.id)
            self.assertEqual(vol.project, another_project)
            self.assertEqual(vol.shared_to_project, True)

    def test_reassign_vm_backends(self, mrapi):
        volume = mfactory.VolumeFactory()
        vm = volume.machine
        original_project = vm.project
        another_project = "another_project"
        with mocked_quotaholder():
            vm = servers.reassign(vm.id, another_project, False,
                                  credentials=self.credentials)
            self.assertEqual(vm.project, another_project)
            self.assertEqual(vm.shared_to_project, False)
            vol = vm.volumes.get(id=volume.id)
            self.assertNotEqual(vol.project, another_project)

        backend = vm.backend
        backend.public = False
        backend.save()
        with mocked_quotaholder():
            self.assertRaises(faults.Forbidden, servers.reassign, vm.id,
                              original_project, False,
                              credentials=self.credentials)
            self.assertEqual(vm.project, another_project)
            self.assertEqual(vm.shared_to_project, False)
            vol = vm.volumes.get(id=volume.id)
            self.assertNotEqual(vol.project, another_project)

        mfactory.ProjectBackendFactory(project=original_project,
                                       backend=backend)
        with mocked_quotaholder():
            vm = servers.reassign(vm.id, original_project, False,
                                  credentials=self.credentials)
            self.assertEqual(vm.project, original_project)
            self.assertEqual(vm.shared_to_project, False)
            vol = vm.volumes.get(id=volume.id)
            self.assertEqual(vol.project, original_project)

    def test_reassign_vm_flavors(self, mrapi):
        volume = mfactory.VolumeFactory()
        vm = volume.machine
        vm_id = vm.id
        original_project = vm.project
        another_project = "another_project"
        with mocked_quotaholder():
            servers.reassign(vm_id, another_project, False,
                             credentials=self.credentials)
            vm = models.VirtualMachine.objects.get(id=vm_id)
            self.assertEqual(vm.project, another_project)
            self.assertEqual(vm.shared_to_project, False)
            vol = vm.volumes.get(id=volume.id)
            self.assertNotEqual(vol.project, another_project)

        vm = models.VirtualMachine.objects.get(id=vm_id)
        flavor = vm.flavor
        flavor.public = False
        flavor.save()
        with mocked_quotaholder():
            self.assertRaises(faults.Forbidden, servers.reassign, vm_id,
                              original_project, False, self.credentials)
            vm = models.VirtualMachine.objects.get(id=vm_id)
            self.assertEqual(vm.project, another_project)
            self.assertEqual(vm.shared_to_project, False)
            vol = vm.volumes.get(id=volume.id)
            self.assertNotEqual(vol.project, another_project)

        mfactory.FlavorAccessFactory(project=original_project,
                                     flavor=flavor)
        with mocked_quotaholder():
            servers.reassign(vm_id, original_project, False,
                             credentials=self.credentials)
            vm = models.VirtualMachine.objects.get(id=vm_id)
            self.assertEqual(vm.project, original_project)
            self.assertEqual(vm.shared_to_project, False)
            vol = vm.volumes.get(id=volume.id)
            self.assertEqual(vol.project, original_project)


class ServerRescueTest(TransactionTestCase):

    def setUp(self):
        self.debian_rescue_image = mfactory.RescueImageFactory(
            target_os_family='linux', target_os='debian',
            location='test-path.iso', name='Test Rescue Image')
        self.windows_rescue_image = mfactory.RescueImageFactory(
            target_os_family='windows', target_os='windows',
            location='test-path-win.iso', name='Test Windows Rescue Image',
            is_default=True)
        self.vm = mfactory.VirtualMachineFactory()
        self.credentials = Credentials("test")

    def test_rescue_started_vm(self):
        """Test rescue a started VM"""
        with mocked_quotaholder():
            self.vm.task = None
            self.vm.operstate = "STARTED"
            print(self.credentials)
            with self.assertRaises(faults.BadRequest):
                servers.rescue(self.vm, credentials=self.credentials)

    def test_rescue_stopped_rescued_vm(self):
        """Test rescue a stopped VM while in rescue mode"""
        with mocked_quotaholder():
            self.vm.task = None
            self.vm.operstate = "STOPPED"
            self.vm.rescue = True
            with self.assertRaises(faults.BadRequest):
                servers.rescue(self.vm, credentials=self.credentials)

    @patch("synnefo.logic.rapi_pool.GanetiRapiClient")
    def test_rescue_stopped_vm(self, mrapi):
        """Test rescue a stopped VM"""
        mrapi().ModifyInstance.return_value = 1
        # Since we are not using rescue properties, the default
        # image should be used.
        with mocked_quotaholder():
            self.vm.task = None
            self.vm.rescue = False
            self.vm.operstate = "STOPPED"
            servers.rescue(self.vm, credentials=self.credentials)
            self.assertEqual(self.vm.task_job_id, 1)
            self.assertFalse(self.vm.rescue_image is None)
            self.assertTrue(self.vm.rescue_image.is_default)

    def test_unrescue_started_vm(self):
        """Test unrescue a started VM"""
        with mocked_quotaholder():
            self.vm.task = None
            self.vm.operstate = "STARTED"
            with self.assertRaises(faults.BadRequest):
                servers.unrescue(self.vm, credentials=self.credentials)

    def test_unrescue_stopped_unrescued_vm(self):
        """Test unrescue a VM that is not in rescue mode"""
        with mocked_quotaholder():
            self.vm.operstate = "STOPPED"
            self.vm.rescue = False
            with self.assertRaises(faults.BadRequest):
                servers.unrescue(self.vm, credentials=self.credentials)

    @patch("synnefo.logic.rapi_pool.GanetiRapiClient")
    def test_unrescue_stopped_vm(self, mrapi):
        """Test unrescue a stopped VM in rescue mode"""
        mrapi().ModifyInstance.return_value = 1
        with mocked_quotaholder():
            self.vm.task = None
            self.vm.operstate = "STOPPED"
            self.vm.rescue = True
            self.vm.rescue_image = self.debian_rescue_image
            servers.unrescue(self.vm, credentials=self.credentials)
            self.assertEqual(self.vm.task_job_id, 1)

    @patch("synnefo.logic.rapi_pool.GanetiRapiClient")
    def test_rescue_vm_rescue_properties(self, mrapi):
        """Test rescue a VM using rescue properties"""
        mrapi().ModifyInstance.return_value = 1
        vm = mfactory.VirtualMachineFactory(
             rescue_properties__os_family='linux',
             rescue_properties__os='debian')
        with mocked_quotaholder():
            vm.task = None
            vm.operstate = "STOPPED"
            servers.rescue(vm, credentials=self.credentials)
            self.assertEqual(vm.task_job_id, 1)
            self.assertEqual(vm.rescue_image, self.debian_rescue_image)
