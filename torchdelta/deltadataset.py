import math

import numpy as np
from deltalake import DeltaTable
from torch.utils.data import get_worker_info, Dataset, IterableDataset
import pyarrow.dataset as ds
import pyarrow.compute as pc
from typing import List, Optional, Callable

from torchvision.datasets import VisionDataset


class DeltaIterableDataset(IterableDataset):
    def __init__(
        self,
        path: str,
        #fields: List[str],
        id_field: str,
        src_field:str,
        target_field:str,
        batch_size: int = None,
        apply_src_numpy_shape=None,
        transform:Optional[Callable] = None,
        target_transform:Optional[Callable] = None,
        use_fixed_rank: bool = False,
        fixed_rank: int = None,
        num_ranks: int = None,
    ) -> None:
        """

        :param path:
        :param fields:
        :param id_field:
        :param batch_size:
        :param return_type: "pylist", "pydict", "pandas"
        :param key_field:
        :param val_field:
        """
        super().__init__()
        self.path = path
        self.fields = {src_field:ds.field(src_field), target_field:ds.field(target_field)} #{field_name: ds.field(field_name) for field_name in fields}
        self.id_field = id_field
        self.src_field = src_field
        self.target_field = target_field
        self.use_fixed_rank = use_fixed_rank
        self.fixed_rank = fixed_rank
        self.num_ranks = num_ranks
        self.batch_size = batch_size
        self.apply_src_numpy_shape = apply_src_numpy_shape
        self.transform = transform
        self.target_transform = target_transform

        self.delta_table = None
        self.scanner = None
        self.start = 0
        self.end = self.count()
        self.delta_table = None
        self.scanner = None

    def count(self):
        _cnt = 0
        for rb in self.init_scanner_and_apply_rank().to_reader():
            _cnt += rb.num_rows
        return _cnt
    def init_scanner_and_apply_rank(self):
        _info = get_worker_info()
        if self.use_fixed_rank:
            return self.init_scanner(
                use_rank=True, rank=self.fixed_rank, num_ranks=self.num_ranks
            )
        elif _info is not None:
            return self.init_scanner(
                use_rank=True, rank=_info.id, num_ranks=_info.num_workers
            )
        else:
            return self.init_scanner(use_rank=False)

    def init_scanner(self, use_rank: bool = False, rank: int = -1, num_ranks: int = -1):
        if self.delta_table is None:
            self.delta_table = DeltaTable(self.path)
            _filter = None
            if use_rank:
                per_worker = int(math.ceil((self.end - self.start) / float(num_ranks)))
                iter_start = self.start + rank * per_worker
                iter_end = min(iter_start + per_worker, self.end)
                _filter = (pc.field(self.id_field) >= pc.scalar(iter_start)) & (pc.field(self.id_field) <= pc.scalar(iter_end))
                #pc.bit_wise_and(pc.field(self.id_field), pc.scalar(num_ranks - 1)) == pc.scalar(rank)

            self.scanner = self.delta_table.to_pyarrow_dataset().scanner(
                columns=self.fields, filter=_filter
            )
        return self.scanner

    def __iter__(self):
        for rb in self.init_scanner_and_apply_rank().to_reader():
            pylist = rb.to_pylist()
            if self.batch_size:
                for i in range(0, len(pylist), self.batch_size):
                    yield pylist[i : i + self.batch_size]
            else:
                for item in pylist:
                    if self.apply_src_numpy_shape is not None:
                        item[self.src_field] = np.frombuffer(item[self.src_field], dtype=np.uint8).reshape(self.apply_src_numpy_shape)
                    if self.transform is not None:
                        item[self.src_field] = self.transform(item[self.src_field])
                    if self.target_transform is not None:
                        item[self.target_field] = self.target_transform(item[self.target_field])
                    yield item[self.src_field], item[self.target_field]

    def __len__(self):
        return self.end
