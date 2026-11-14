"""Write Ultralytics training checkpoints directly to disk, with atomic replacement."""
from copy import deepcopy
from datetime import datetime
import shutil

import torch
from ultralytics.utils.torch_utils import convert_optimizer_state_dict_to_fp16, unwrap_model


class StreamCheckpoints:
    def save_model(self):
        # Stream to disk instead of keeping a serialized checkpoint in memory.
        self.wdir.mkdir(parents=True, exist_ok=True)
        temporary = self.last.with_suffix('.tmp')
        try:
            torch.save({
                'model': None, 'ema': deepcopy(unwrap_model(self.ema.ema)).half(),
                'epoch': self.epoch, 'best_fitness': self.best_fitness, 'updates': self.ema.updates,
                'optimizer': convert_optimizer_state_dict_to_fp16(deepcopy(self.optimizer.state_dict())),
                'scaler': self.scaler.state_dict(), 'train_args': vars(self.args),
                'train_results': self.read_results_csv(), 'train_metrics': {**self.metrics, 'fitness': self.fitness},
                'date': datetime.now().isoformat(),
            }, temporary)
            if self.best_fitness == self.fitness or not self.best.exists():
                best_temporary = self.best.with_suffix('.tmp')
                try:
                    shutil.copyfile(temporary, best_temporary)
                    best_temporary.replace(self.best)
                finally:
                    best_temporary.unlink(missing_ok=True)
            if self.save_period > 0 and self.epoch % self.save_period == 0:
                epoch_file = self.wdir / f'epoch{self.epoch}.pt'
                shutil.copyfile(temporary, epoch_file)
            temporary.replace(self.last)
        finally:
            temporary.unlink(missing_ok=True)

