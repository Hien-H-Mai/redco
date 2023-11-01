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
import jax
import jax.numpy as jnp
import numpy as np
import optax
from datasets import load_dataset
from transformers import AutoTokenizer, FlaxAutoModelForSequenceClassification
from redco import Deployer, Trainer


def collate_fn(
        examples, sent0_key, sent1_key, label_key, tokenizer, max_length):
    texts = []
    for example in examples:
        if sent1_key is None:
            texts.append(example[sent0_key])
        else:
            texts.append((example[sent0_key], example[sent1_key]))

    batch = tokenizer(
        texts,
        max_length=max_length,
        padding='max_length',
        truncation=True,
        return_tensors='np')

    batch['labels'] = np.array([example[label_key] for example in examples])

    return batch


def loss_fn(train_rng, state, params, batch, is_training, is_regression):
    labels = batch.pop('labels')

    logits = state.apply_fn(
        **batch, params=params, dropout_rng=train_rng, train=is_training).logits

    if is_regression:
        return jnp.mean(jnp.square(logits[..., 0] - labels))
    else:
        return optax.softmax_cross_entropy_with_integer_labels(
            logits=logits, labels=labels).mean()


def pred_fn(pred_rng, params, batch, model, is_regression):
    batch.pop('labels')

    logits = model(**batch, params=params, train=False).logits

    if is_regression:
        return logits[..., 0]
    else:
        return logits.argmax(axis=-1)


def eval_metric_fn(examples, preds, label_key, is_regression):
    preds = np.array(preds)
    labels = np.array([example[label_key] for example in examples])

    if is_regression:
        return {'square error': np.mean(np.square(preds - labels))}
    else:
        return {'acc': np.mean(preds == labels).item()}


def main(n_processes=1,
         host0_address=None,
         host0_port=11111,
         process_id=None,
         dataset_name='sst2',
         sent0_key='sentence',
         sent1_key=None,
         label_key='label',
         is_regression=False,
         model_name_or_path='roberta-base',
         n_model_shards=2,
         max_length=512,
         n_epochs=2,
         per_device_batch_size=4,
         eval_per_device_batch_size=8,
         accumulate_grad_batches=2,
         learning_rate=1e-5,
         warmup_rate=0.1,
         weight_decay=0.,
         jax_seed=42,
         workdir='./workdir',
         run_tensorboard=False):
    deployer = Deployer(
        n_model_shards=n_model_shards,
        jax_seed=jax_seed,
        workdir=workdir,
        run_tensorboard=run_tensorboard,
        n_processes=n_processes,
        host0_address=host0_address,
        host0_port=host0_port,
        process_id=process_id)

    dataset = load_dataset('glue', dataset_name)
    dataset = {key: list(dataset[key]) for key in dataset.keys()}

    num_labels = 1 if is_regression \
        else len(set([example[label_key] for example in dataset['train']]))

    tokenizer = AutoTokenizer.from_pretrained(model_name_or_path)

    with jax.default_device(jax.devices('cpu')[0]):
        model = FlaxAutoModelForSequenceClassification.from_pretrained(
            model_name_or_path, num_labels=num_labels)
        model.params = model.to_fp32(model.params)

    lr_schedule_fn = deployer.get_lr_schedule_fn(
        train_size=len(dataset['train']),
        per_device_batch_size=per_device_batch_size,
        n_epochs=n_epochs,
        learning_rate=learning_rate,
        schedule_type='linear',
        warmup_rate=warmup_rate)
    optimizer = optax.adamw(
        learning_rate=lr_schedule_fn, weight_decay=weight_decay)
    if accumulate_grad_batches > 1:
        optimizer = optax.MultiSteps(
            optimizer, every_k_schedule=accumulate_grad_batches)

    trainer = Trainer(
        deployer=deployer,
        collate_fn=partial(
            collate_fn,
            sent0_key=sent0_key,
            sent1_key=sent1_key,
            label_key=label_key,
            tokenizer=tokenizer,
            max_length=max_length),
        apply_fn=model,
        loss_fn=partial(loss_fn, is_regression=is_regression),
        params=model.params,
        optimizer=optimizer,
        lr_schedule_fn=lr_schedule_fn,
        params_sharding_rules=deployer.get_sharding_rules(params=model.params))

    predictor = trainer.get_default_predictor(
        pred_fn=partial(pred_fn, model=model, is_regression=is_regression))

    trainer.fit(
        train_examples=dataset['train'],
        per_device_batch_size=per_device_batch_size,
        n_epochs=n_epochs,
        eval_examples=dataset['validation'],
        eval_per_device_batch_size=eval_per_device_batch_size,
        eval_loss=True,
        eval_predictor=predictor,
        eval_metric_fn=partial(
            eval_metric_fn, label_key=label_key, is_regression=is_regression))


if __name__ == '__main__':
    fire.Fire(main)
