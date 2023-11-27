#  Copyright 2021 Google LLC
#  #
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  #
#      https://www.apache.org/licenses/LICENSE-2.0
#  #
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from functools import partial
import fire
import tqdm
import numpy as np
from sklearn.metrics import confusion_matrix
import jax
import jax.numpy as jnp
from flax.core.frozen_dict import freeze
import optax
from redco import Deployer, Trainer, Predictor

from data_utils import get_dataset
from model_utils import CNN, collate_fn, loss_fn, pred_fn


class FedAvgServer:
    def __init__(self, deployer, model, params, n_clients):
        self._deployer = deployer
        self._model = model
        self._params = freeze(params)
        self._n_clients = n_clients

        self._trainer = Trainer(
            deployer=self._deployer,
            collate_fn=collate_fn,
            apply_fn=self._model.apply,
            loss_fn=loss_fn,
            params=params,
            optimizer=optax.adam(learning_rate=0.))

        self._predictor = Predictor(
            deployer=self._deployer,
            collate_fn=collate_fn,
            pred_fn=partial(pred_fn, model=self._model),
            output_fn=lambda x: x.tolist())

    def train_client(self,
                     examples,
                     learning_rate,
                     per_device_batch_size,
                     n_epochs):
        self._trainer.set_train_state(
            apply_fn=self._model.apply,
            params=self._params,
            optimizer=optax.adam(learning_rate=learning_rate),
            step=0)

        print(f'train size: {len(examples)}')

        self._trainer.fit(
            train_examples=examples,
            per_device_batch_size=per_device_batch_size,
            n_epochs=n_epochs)

        return self._trainer.params

    def test(self, examples, per_device_batch_size):
        preds = self._predictor.predict(
            examples=examples,
            per_device_batch_size=per_device_batch_size,
            params=self._params)
        labels = [example['label'] for example in examples]

        acc = np.mean(np.array(preds) == np.array(labels))
        conf_mat = confusion_matrix(y_true=labels, y_pred=preds)

        return acc, conf_mat

    def run(self,
            n_rounds,
            n_clients_per_round,
            n_client_epochs_per_round,
            per_device_batch_size,
            eval_per_device_batch_size,
            learning_rate,
            client_train_datasets,
            eval_dataset):
        for round_idx in tqdm.trange(n_rounds, desc='Server Round'):
            round_client_idxes = np.random.choice(
                np.arange(self._n_clients), n_clients_per_round, replace=False)

            sum_client_params = jax.tree_util.tree_map(
                jnp.zeros_like, self._params)
            for client_idx in round_client_idxes:
                client_params = self.train_client(
                    examples=client_train_datasets[client_idx],
                    learning_rate=learning_rate,
                    per_device_batch_size=per_device_batch_size,
                    n_epochs=n_client_epochs_per_round)

                sum_client_params = jax.tree_util.tree_map(
                    lambda x, y: x + y, sum_client_params, client_params)

            self._params = jax.tree_util.tree_map(
                lambda x: x / n_clients_per_round, sum_client_params)

            acc, conf_mat = self.test(
                examples=eval_dataset,
                per_device_batch_size=eval_per_device_batch_size)

            print(f'Round {round_idx} finished.')
            print(f'Test accuracy: {acc}')
            print(f'Confusion matrix:\n {conf_mat}')


def main(data_dir='./data',
         dataset_name='MNIST',
         n_clients=100,
         n_data_shards=200,
         n_rounds=100,
         n_clients_per_round=10,
         n_client_epochs_per_round=5,
         per_device_batch_size=10,
         eval_per_device_batch_size=100,
         learning_rate=0.001,
         jax_seed=42):
    deployer = Deployer(jax_seed=jax_seed, verbose=False)

    client_train_datasets, eval_dataset = get_dataset(
        data_dir=data_dir,
        dataset_name=dataset_name,
        n_clients=n_clients,
        n_data_shards=n_data_shards)

    model = CNN()
    dummy_batch = collate_fn([eval_dataset[0]])
    params = model.init(deployer.gen_rng(), dummy_batch['images'])['params']

    server = FedAvgServer(
        deployer=deployer, model=model, params=params, n_clients=n_clients)

    server.run(
        n_rounds=n_rounds,
        n_clients_per_round=n_clients_per_round,
        n_client_epochs_per_round=n_client_epochs_per_round,
        per_device_batch_size=per_device_batch_size,
        eval_per_device_batch_size=eval_per_device_batch_size,
        learning_rate=learning_rate,
        client_train_datasets=client_train_datasets,
        eval_dataset=eval_dataset)


if __name__ == '__main__':
    fire.Fire(main)