export function datasetLabel(dataset: {name?: string; id?: string}): string {
  return dataset.name && dataset.name !== dataset.id ? dataset.name : 'Исходный набор данных';
}
